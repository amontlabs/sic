"""Live pipeline: audio arrives in real time -> segments -> claims -> verdicts, as events.

Used with a media file played at real speed (fake live). In production the audio source
would be the broadcast stream; everything downstream is identical.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np

from sic import asr, config, speakers
from sic.claims import detect_claims, similar
from sic.diarize import Embedder, OnlineSpeakerTracker
from sic.llm import LLM
from sic.models import Claim, Debate, Event, Segment, Verdict
from sic.sources.base import VerifyContext
from sic.text import build_segments
from sic.verify import Verifier

log = logging.getLogger("sic.live")

SR = 16000
Emit = Callable[[Event], Awaitable[None]]


class Clock:
    """Media-time clock: media seconds = start + elapsed wall seconds * speed."""

    def __init__(self, start: float = 0.0, speed: float = 1.0) -> None:
        self.start = start
        self.speed = speed
        self.t0 = time.monotonic()

    def now(self) -> float:
        return self.start + (time.monotonic() - self.t0) * self.speed

    async def wait_until(self, media_t: float) -> None:
        delay = (media_t - self.now()) / self.speed
        if delay > 0:
            await asyncio.sleep(delay)


def make_vad():
    import sherpa_onnx as so

    cfg = so.VadModelConfig(
        silero_vad=so.SileroVadModelConfig(
            model=str(config.MODELS_DIR / "silero_vad.onnx"), min_silence_duration=0.35, min_speech_duration=0.25,
            max_speech_duration=12.0, threshold=0.5,
        ),
        sample_rate=SR,
    )
    return so.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)


def drain_vad(vad, offset: float) -> list[tuple[np.ndarray, float]]:
    """Pop finished speech segments. Samples MUST be copied before pop(): the segment
    object points into the detector's buffer, which pop() releases (empty audio otherwise)."""
    out = []
    while not vad.empty():
        seg = vad.front
        samples = np.array(seg.samples, dtype=np.float32)
        start = offset + seg.start / SR
        vad.pop()
        out.append((samples, start))
    return out


class LivePipeline:
    """Real-time pipeline. Guarantees:
    - claims are only published once their voice has a name (held until then, re-published if a
      name changes, and re-verified because the speaker matters for verification);
    - naming and claim detection never block the audio feed (they run as background tasks);
    - verification is a priority queue (precise figures/votes first); low-priority claims that
      waited too long are dropped rather than delivered stale.
    """

    def __init__(
        self,
        debate: Debate,
        llm: LLM,
        verifier: Verifier | None,
        emit: Emit,
        strict: bool = True,
        claim_model: str | None = None,
        verify_concurrency: int = 4,
        detect_timeout: float = 90.0,
        max_backlog_age: float = 600.0,
    ) -> None:
        self.debate = debate
        self.llm = llm
        self.verifier = verifier
        self._emit = emit
        self.strict = strict
        self.claim_model = claim_model or config.LLM_FAST
        self.detect_timeout = detect_timeout
        self.max_backlog_age = max_backlog_age
        self.tracker = OnlineSpeakerTracker(threshold=0.45)
        self.embedder = Embedder()
        self.segments: list[Segment] = []
        self.mapping: dict[str, str] = {}
        self.claims: dict[str, Claim] = {}
        self.verdicts: dict[str, Verdict] = {}
        self.dropped: list[str] = []
        self._cluster_of: dict[str, str] = {}  # claim id -> voice cluster
        self._held: list[Claim] = []  # claims whose voice has no name yet
        self._seq = 0
        self._pending_focus: list[Segment] = []
        self._detect_lock = asyncio.Lock()
        self._name_lock = asyncio.Lock()
        self._naming: asyncio.Task | None = None
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._qseq = 0
        self._workers: list[asyncio.Task] = []
        self._verify_concurrency = verify_concurrency
        self._tasks: set[asyncio.Task] = set()
        self._last_naming = 0.0
        self.clock = Clock()
        self.people = {p.id: p for p in debate.roster}

    async def emit(self, type_: str, t: float, data: dict) -> None:
        self._seq += 1
        await self._emit(Event(seq=self._seq, type=type_, t=round(t, 3), at=round(self.clock.now(), 3), data=data))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def aclose(self) -> None:
        """Cancel every background task (client disconnected / shutdown)."""
        tasks = [*self._tasks, *self._workers]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._workers.clear()

    async def drain(self) -> None:
        """Wait until detection and the verification queue are done."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._workers:
            await self._queue.join()

    # -- audio -> segments ------------------------------------------------------------------------

    async def run_file(self, wav: str | Path, start: float = 0.0, duration: float | None = None, speed: float = 1.0) -> None:
        audio = asr.load_audio(wav, start, duration)
        self.clock = Clock(start, speed)
        vad = make_vad()
        await self.emit("status", start, {"state": "start", "from": start, "speed": speed})
        step = int(0.5 * SR)
        fed = 0
        try:
            while fed < len(audio):
                chunk = audio[fed : fed + step]
                fed += len(chunk)
                await self.clock.wait_until(start + fed / SR)  # real-time pacing
                vad.accept_waveform(chunk)
                for samples, t in drain_vad(vad, start):
                    await self._utterance(samples, t)
            vad.flush()
            for samples, t in drain_vad(vad, start):
                await self._utterance(samples, t)
            await self._detect(force=True)
            await self.drain()
            await self.emit("status", self.clock.now(), {"state": "end"})
        finally:
            await self.aclose()

    async def _utterance(self, samples: np.ndarray, t: float) -> None:
        dur = len(samples) / SR
        if dur < 0.3:
            return
        names = [p.name for p in self.debate.roster]
        words = await asyncio.to_thread(asr.transcribe, samples, t, names)
        if not words:
            return
        emb = await asyncio.to_thread(self.embedder.embed, samples)
        cluster = self.tracker.assign(emb, dur)
        segs = build_segments(words, [cluster] * len(words), id_prefix="s", start_index=len(self.segments))
        for s in segs:
            s.speaker = self.mapping.get(cluster, cluster)
            raw = s.model_copy(update={"speaker": cluster})
            self.segments.append(raw)
            self._pending_focus.append(raw)
            await self.emit("segment", s.start, s.model_dump(exclude={"words"}) | {"words": [], "cluster": cluster})
        self._maybe_name()
        await self._detect()

    # -- naming -----------------------------------------------------------------------------------

    def _maybe_name(self) -> None:
        """Periodic naming in the background (never blocks the audio feed)."""
        speech = sum(s.end - s.start for s in self.segments)
        unknown = any(c.id not in self.mapping for c in self.tracker.clusters)
        if speech - self._last_naming < (20 if unknown else 120) or len(self.segments) < 4:
            return
        if self._naming is None or self._naming.done():
            self._last_naming = speech
            self._naming = self._spawn(self._name())

    async def _name(self) -> None:
        async with self._name_lock:
            try:
                mapping = await asyncio.wait_for(speakers.name_speakers(self.segments, self.debate, self.llm), 60)
            except Exception as e:
                log.warning("speaker naming failed: %s", e)
                return
            if mapping == self.mapping:
                return
            old, self.mapping = self.mapping, mapping
            await self.emit("speaker", self.clock.now(), {"mapping": mapping})
            await self._on_mapping_change(old)

    async def _on_mapping_change(self, old: dict[str, str]) -> None:
        held, self._held = self._held, []
        for c in held:
            cluster = self._cluster_of.get(c.id, "")
            if self.mapping.get(cluster) in self.people:
                await self._publish(c)
            else:
                self._held.append(c)
        for cid, c in list(self.claims.items()):
            cluster = self._cluster_of.get(cid)
            new = self.mapping.get(cluster or "")
            if cluster and new and new != c.speaker and old.get(cluster) != new:
                updated = c.model_copy(update={"speaker": new})
                self.claims[cid] = updated
                await self.emit("claim", updated.start, updated.model_dump() | {"cluster": cluster, "revised": True})
                self.verdicts.pop(cid, None)
                self._enqueue(updated)

    # -- claims & verdicts ------------------------------------------------------------------------

    async def _detect(self, force: bool = False) -> None:
        focus = self._pending_focus
        if not focus:
            return
        speech = focus[-1].end - focus[0].start
        turn_change = len({s.speaker for s in focus[-3:]}) > 1
        if not force and speech < 25 and not (turn_change and speech > 8):
            return
        self._pending_focus = []
        ctx_start = focus[0].start - 60
        context = [s for s in self.segments if ctx_start <= s.start < focus[0].start]
        self._spawn(self._detect_window(focus, context))

    async def _detect_window(self, focus: list[Segment], context: list[Segment]) -> None:
        async with self._detect_lock:
            if any(s.speaker not in self.mapping for s in focus):
                await self._name()  # name new voices before attributing claims to them
            named = speakers.apply_mapping(focus, self.mapping)
            named_ctx = speakers.apply_mapping(context, self.mapping)
            try:
                found = await asyncio.wait_for(
                    detect_claims(named, named_ctx, self.debate, self.llm, self.claim_model, id_prefix="c"), self.detect_timeout
                )
            except Exception as e:
                log.warning("claim detection failed: %s", e)
                return
            raw_by_id = {s.id: s.speaker for s in focus}
            for c in found:
                cluster = raw_by_id.get(c.segment_ids[0]) if c.segment_ids else None
                if cluster:
                    self._cluster_of[c.id] = cluster
                if any(o.speaker == c.speaker and similar(o.claim, c.claim) >= 0.8 for o in self.claims.values()):
                    continue
                if c.speaker not in self.people:
                    if cluster:
                        self._held.append(c)  # voice not (confidently) named yet: wait, never publish anonymously
                    continue
                await self._publish(c)

    async def _publish(self, c: Claim) -> None:
        cluster = self._cluster_of.get(c.id)
        if cluster and c.speaker not in self.people:
            c = c.model_copy(update={"speaker": self.mapping.get(cluster, c.speaker)})
        person = self.people.get(c.speaker)
        if person is None or person.role == "moderator":
            return
        self.claims[c.id] = c
        await self.emit("claim", c.start, c.model_dump() | {"cluster": cluster})
        self._enqueue(c)

    def _enqueue(self, c: Claim) -> None:
        if self.verifier is None:
            return
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self._verify_concurrency)]
        self._qseq += 1
        self._queue.put_nowait((c.priority, -c.start, self._qseq, c.id))

    async def _worker(self) -> None:
        assert self.verifier is not None
        while True:
            _prio, _neg_start, _n, cid = await self._queue.get()
            try:
                c = self.claims.get(cid)
                if c is None:
                    continue
                if c.priority >= 3 and self.clock.now() - c.end > self.max_backlog_age:
                    self.dropped.append(cid)  # stale minor claim: skip rather than deliver late
                    continue
                ctx = VerifyContext(as_of=self.debate.date if self.strict else None, debate=self.debate, speaker=self.people.get(c.speaker))
                v = await self.verifier.verify(c, ctx)
                if self.claims.get(cid) is not c:
                    continue  # claim was revised (speaker renamed) while verifying: the new version is queued
                self.verdicts[cid] = v
                await self.emit("verdict", c.start, v.model_dump())
            except Exception as e:
                log.warning("verification of %s failed: %s", cid, e)
            finally:
                self._queue.task_done()


async def record(debate_id: str, start: float, minutes: float, speed: float = 1.0, strict: bool = True) -> dict:
    """Run the real live pipeline on part of a debate and save the event log + latency stats."""
    import json

    from sic.ingest import audio_path
    from sic.library import Library
    from sic.pipeline import load_debate, run_dir
    from sic.sources import build_connectors

    debate = load_debate(debate_id)
    events: list[Event] = []

    async def emit(e: Event) -> None:
        events.append(e)
        if e.type in ("claim", "verdict"):
            label = e.data.get("label") or ""
            print(f"[{e.at:8.1f}] {e.type:7s} t={e.t:8.1f} {label:12s} {(e.data.get('claim') or e.data.get('headline') or '')[:90]}", flush=True)

    llm = LLM(max_attempts=3, timeout=60)
    try:
        verifier = Verifier(build_connectors(debate), llm, Library.load())
        pipe = LivePipeline(debate, llm, verifier, emit, strict=strict)
        await pipe.run_file(audio_path(debate), start=start, duration=minutes * 60, speed=speed)
    finally:
        await llm.aclose()
    claim_end = {e.data["id"]: e.data["end"] for e in events if e.type == "claim"}
    claim_lat = sorted(e.at - e.data["end"] for e in events if e.type == "claim")
    verdict_lat = sorted(e.at - claim_end[e.data["claim_id"]] for e in events if e.type == "verdict" and e.data["claim_id"] in claim_end)
    seg_lat = sorted(e.at - e.data["end"] for e in events if e.type == "segment")

    def pct(xs: list[float], p: float) -> float | None:
        return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None

    stats = {
        "from": start, "minutes": minutes, "speed": speed,
        "segments": len(seg_lat), "claims": len(claim_lat), "verdicts": len(verdict_lat),
        "segment_latency_p50": pct(seg_lat, 0.5), "segment_latency_p90": pct(seg_lat, 0.9),
        "claim_latency_p50": pct(claim_lat, 0.5), "claim_latency_p90": pct(claim_lat, 0.9),
        "verdict_latency_p50": pct(verdict_lat, 0.5), "verdict_latency_p90": pct(verdict_lat, 0.9),
        "llm_usage": dict(llm.usage),
    }
    out = run_dir(debate_id) / f"live-{int(start)}-{int(minutes)}m.json"
    out.write_text(json.dumps({"stats": stats, "events": [e.model_dump() for e in events]}, ensure_ascii=False))
    return stats


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="sic.live", description="Record a real live run on part of a debate")
    ap.add_argument("debate_id")
    ap.add_argument("--from", dest="start", type=float, default=0.0)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(json.dumps(asyncio.run(record(a.debate_id, a.start, a.minutes, a.speed)), indent=1))


if __name__ == "__main__":
    main()
