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


class LivePipeline:
    def __init__(
        self,
        debate: Debate,
        llm: LLM,
        verifier: Verifier | None,
        emit: Emit,
        strict: bool = True,
        claim_model: str | None = None,
        verify_concurrency: int = 4,
    ) -> None:
        self.debate = debate
        self.llm = llm
        self.verifier = verifier
        self._emit = emit
        self.strict = strict
        self.claim_model = claim_model or config.LLM_FAST
        self.tracker = OnlineSpeakerTracker(threshold=0.45)
        self.embedder = Embedder()
        self.segments: list[Segment] = []
        self.mapping: dict[str, str] = {}
        self.claims: list[Claim] = []
        self.verdicts: list[Verdict] = []
        self._seq = 0
        self._pending_focus: list[Segment] = []
        self._detect_lock = asyncio.Lock()
        self._verify_sem = asyncio.Semaphore(verify_concurrency)
        self._tasks: set[asyncio.Task] = set()
        self._last_naming = 0.0
        self.clock = Clock()
        self.people = {p.id: p for p in debate.roster}

    async def emit(self, type_: str, t: float, data: dict) -> None:
        self._seq += 1
        await self._emit(Event(seq=self._seq, type=type_, t=round(t, 3), at=round(self.clock.now(), 3), data=data))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- audio -> segments ------------------------------------------------------------------------

    async def run_file(self, wav: str | Path, start: float = 0.0, duration: float | None = None, speed: float = 1.0) -> None:
        import sherpa_onnx as so

        audio = asr.load_audio(wav, start, duration)
        self.clock = Clock(start, speed)
        vad_cfg = so.VadModelConfig(
            silero_vad=so.SileroVadModelConfig(
                model=str(config.MODELS_DIR / "silero_vad.onnx"), min_silence_duration=0.35, min_speech_duration=0.25,
                max_speech_duration=12.0, threshold=0.5,
            ),
            sample_rate=SR,
        )
        vad = so.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=60)
        await self.emit("status", start, {"state": "start", "from": start, "speed": speed})
        step = int(0.5 * SR)
        fed = 0
        while fed < len(audio):
            chunk = audio[fed : fed + step]
            fed += len(chunk)
            await self.clock.wait_until(start + fed / SR)  # real-time pacing
            vad.accept_waveform(chunk)
            while not vad.empty():
                seg = vad.front
                vad.pop()
                await self._utterance(np.array(seg.samples, dtype=np.float32), start + seg.start / SR)
        vad.flush()
        while not vad.empty():
            seg = vad.front
            vad.pop()
            await self._utterance(np.array(seg.samples, dtype=np.float32), start + seg.start / SR)
        await self._detect(force=True)
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self.emit("status", self.clock.now(), {"state": "end"})

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
        await self._maybe_name()
        await self._detect()

    async def _maybe_name(self, force: bool = False) -> None:
        speech = sum(s.end - s.start for s in self.segments)
        unknown = any(c.id not in self.mapping for c in self.tracker.clusters)
        if not force and (speech - self._last_naming < (20 if unknown else 120) or len(self.segments) < 4):
            return
        self._last_naming = speech
        try:
            mapping = await speakers.name_speakers(self.segments, self.debate, self.llm)
        except Exception as e:
            log.warning("speaker naming failed: %s", e)
            return
        if mapping != self.mapping:
            self.mapping = mapping
            await self.emit("speaker", self.clock.now(), {"mapping": mapping})

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
        if any(s.speaker not in self.mapping for s in focus):
            await self._maybe_name(force=True)  # never emit claims attributed to an anonymous voice
        named = speakers.apply_mapping(focus, self.mapping)
        ctx_start = named[0].start - 60
        context = speakers.apply_mapping([s for s in self.segments if ctx_start <= s.start < named[0].start], self.mapping)
        coro = self._detect_window(named, context)
        if force:
            await coro
        else:
            self._spawn(coro)

    async def _detect_window(self, focus: list[Segment], context: list[Segment]) -> None:
        async with self._detect_lock:
            try:
                found = await detect_claims(focus, context, self.debate, self.llm, self.claim_model, id_prefix="c")
            except Exception as e:
                log.warning("claim detection failed: %s", e)
                return
            for c in found:
                if any(o.speaker == c.speaker and similar(o.claim, c.claim) >= 0.8 for o in self.claims):
                    continue
                if self.people.get(c.speaker) and self.people[c.speaker].role == "moderator":
                    continue
                self.claims.append(c)
                await self.emit("claim", c.start, c.model_dump())
                if self.verifier is not None:
                    self._spawn(self._verify(c))

    async def _verify(self, c: Claim) -> None:
        assert self.verifier is not None
        async with self._verify_sem:
            ctx = VerifyContext(as_of=self.debate.date if self.strict else None, debate=self.debate, speaker=self.people.get(c.speaker))
            v = await self.verifier.verify(c, ctx)
        self.verdicts.append(v)
        await self.emit("verdict", c.start, v.model_dump())
