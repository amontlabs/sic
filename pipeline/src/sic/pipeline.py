"""Offline pipeline: media file -> transcript -> speakers -> claims -> verdicts -> Run.

Every stage is cached in data/runs/<debate_id>/ so runs are resumable and cheap to re-run.

    uv run python -m sic.pipeline run 2022-macron-lepen
    uv run python -m sic.pipeline run 2022-macron-lepen --minutes 20      # first 20 minutes only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from sic import asr, config, diarize, speakers
from sic.claims import detect_all
from sic.ingest import audio_path
from sic.library import Library
from sic.llm import LLM
from sic.models import Claim, Debate, Event, Run, Segment, Verdict, Word
from sic.sources import build_connectors
from sic.sources.base import VerifyContext
from sic.text import build_segments
from sic.verify import VERIFIER_VERSION, Verifier, dedupe_sources

log = logging.getLogger("sic.pipeline")

# Assumed live latencies used to place offline results on the simulated live clock.
ASR_LATENCY = 2.0  # utterance end -> segment available
CLAIM_LATENCY = 3.0  # segment available -> claim detected

Progress = Callable[[str, float], None]


def load_debate(debate_id: str) -> Debate:
    return Debate.model_validate_json((config.DEBATES / f"{debate_id}.json").read_text())


def run_dir(debate_id: str) -> Path:
    d = config.RUNS / debate_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump(path: Path, items) -> None:
    path.write_text(json.dumps([i.model_dump() if hasattr(i, "model_dump") else i for i in items], ensure_ascii=False))


def stage_transcribe(debate: Debate, d: Path, minutes: float | None, progress: Progress) -> list[Word]:
    out = d / "words.json"
    if out.exists():
        return [Word.model_validate(w) for w in json.loads(out.read_text())]
    wav = audio_path(debate)
    names = [p.name for p in debate.roster]
    if minutes:
        words = asr.transcribe(asr.load_audio(wav, 0, minutes * 60), names=names)
    else:
        words = asr.transcribe_long(wav, names=names, on_progress=lambda p: progress("transcription", p))
    _dump(out, words)
    return words


def stage_diarize(debate: Debate, d: Path, minutes: float | None, progress: Progress) -> list[diarize.Turn]:
    out = d / "turns.json"
    if out.exists():
        return [diarize.Turn(**t) for t in json.loads(out.read_text())]
    wav = audio_path(debate)
    progress("locuteurs", 0.0)
    audio = asr.load_audio(wav, 0, minutes * 60 if minutes else None)
    # Diarize in blocks (clustering cost grows fast with length), then link block clusters globally.
    turns = diarize.diarize_long(audio, num_speakers=len(debate.roster) or None, on_progress=lambda p: progress("locuteurs", p))
    _dump(out, [t.__dict__ for t in turns])
    return turns


def _hash(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def claim_hash(c: Claim, strict: bool) -> str:
    """Identity of a verification request: a cached verdict is only reused for the exact same claim."""
    return _hash(c.id, c.speaker, c.quote, c.claim, strict, VERIFIER_VERSION)


async def stage_speakers(debate: Debate, d: Path, segments: list[Segment], turns: list[diarize.Turn], llm: LLM) -> dict[str, str]:
    out = d / "speakers.json"
    turns_hash = _hash([(t.start, t.end, t.speaker) for t in turns])
    if out.exists():
        cached = json.loads(out.read_text())
        if "mapping" not in cached:  # legacy format: plain mapping
            return cached
        if cached.get("turns_hash") == turns_hash:
            return cached["mapping"]
        log.info("diarization changed since speakers were named: renaming")
    mapping = await speakers.name_speakers(segments, debate, llm)
    out.write_text(json.dumps({"turns_hash": turns_hash, "mapping": mapping}, ensure_ascii=False, indent=1))
    return mapping


async def stage_claims(debate: Debate, d: Path, segments: list[Segment], llm: LLM, progress: Progress) -> list[Claim]:
    out = d / "claims.json"
    if out.exists():
        return [Claim.model_validate(c) for c in json.loads(out.read_text())]
    claims, failed = await detect_all(segments, debate, llm, on_progress=lambda p: progress("affirmations", p))
    if failed:
        log.warning("%d claim window(s) failed; not caching claims so the next run retries them", failed)
    else:
        _dump(out, claims)
    return claims


async def stage_verify(
    debate: Debate, d: Path, claims: list[Claim], verifier: Verifier, strict: bool, progress: Progress, concurrency: int | None = None
) -> list[Verdict]:
    import os

    concurrency = concurrency or int(os.environ.get("SIC_VERIFY_CONCURRENCY", "12"))
    out = d / "verdicts.jsonl"
    expected = {c.id: claim_hash(c, strict) for c in claims}
    done: dict[str, Verdict] = {}
    if out.exists():
        for line in out.read_text().splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                v = Verdict.model_validate(raw)
            except ValueError:
                continue  # torn write from an interrupted run
            h = raw.get("_claim_hash")
            if h is not None and h != expected.get(v.claim_id):
                continue  # verdict was for a different claim that had the same id: re-verify
            done[v.claim_id] = v
    todo = [c for c in claims if c.id not in done and c.checkable]
    people = {p.id: p for p in debate.roster}
    sem = asyncio.Semaphore(concurrency)
    n_total = len(todo)
    n_done = 0

    async def one(c: Claim) -> None:
        nonlocal n_done
        async with sem:
            ctx = VerifyContext(as_of=debate.date if strict else None, debate=debate, speaker=people.get(c.speaker))
            v = await verifier.verify(c, ctx)
        done[c.id] = v
        with out.open("a") as f:
            f.write(json.dumps(v.model_dump(mode="json") | {"_claim_hash": expected[c.id]}, ensure_ascii=False) + "\n")
        n_done += 1
        progress("vérification", n_done / max(1, n_total))

    await asyncio.gather(*(one(c) for c in todo))
    return [dedupe_sources(done[c.id]) for c in claims if c.id in done]


def simulate_events(segments: list[Segment], claims: list[Claim], verdicts: list[Verdict]) -> list[Event]:
    """Place offline results on a live clock: when each item would have been available."""
    raw: list[tuple[float, str, float, dict]] = []
    seg_at: dict[str, float] = {}
    for s in segments:
        seg_at[s.id] = s.end + ASR_LATENCY
        raw.append((seg_at[s.id], "segment", s.start, s.model_dump(exclude={"words"}) | {"words": []}))
    claim_at: dict[str, float] = {}
    for c in claims:
        # a claim can only be detected once all the segments it spans have been transcribed
        avail = max([seg_at[i] for i in c.segment_ids if i in seg_at] or [c.end + ASR_LATENCY])
        at = avail + CLAIM_LATENCY
        claim_at[c.id] = at
        raw.append((at, "claim", c.start, c.model_dump()))
    starts = {c.id: c.start for c in claims}
    for v in verdicts:
        at = claim_at.get(v.claim_id, 0.0) + (v.latency_s if v.latency_s is not None else 8.0)
        raw.append((at, "verdict", starts.get(v.claim_id, 0.0), v.model_dump()))
    raw.sort(key=lambda r: (r[0], ["segment", "claim", "verdict"].index(r[1])))
    return [Event(seq=i + 1, type=typ, t=round(t, 3), at=round(at, 3), data=data) for i, (at, typ, t, data) in enumerate(raw)]


def stats(debate: Debate, segments: list[Segment], claims: list[Claim], verdicts: list[Verdict], llm: LLM | None) -> dict:
    by_speaker: dict[str, Counter] = {}
    vmap = {v.claim_id: v for v in verdicts}
    for c in claims:
        v = vmap.get(c.id)
        by_speaker.setdefault(c.speaker, Counter())[v.label if v else "en_cours"] += 1
    talk = Counter()
    for s in segments:
        talk[s.speaker or "autre"] += s.end - s.start
    lat = sorted(v.latency_s for v in verdicts if v.latency_s is not None)
    return {
        "segments": len(segments),
        "claims": len(claims),
        "verdicts": len(verdicts),
        "by_label": dict(Counter(v.label for v in verdicts)),
        "by_speaker": {k: dict(v) for k, v in by_speaker.items()},
        "talk_time": {k: round(v) for k, v in talk.items()},
        "methods": dict(Counter(v.method for v in verdicts)),
        "latency_p50": lat[len(lat) // 2] if lat else None,
        "latency_p90": lat[int(len(lat) * 0.9)] if lat else None,
        "llm_usage": dict(llm.usage) if llm else None,
    }


async def process(
    debate_id: str,
    minutes: float | None = None,
    strict: bool = True,
    progress: Progress | None = None,
    verify: bool = True,
) -> Run:
    progress = progress or (lambda stage, p: None)
    debate = load_debate(debate_id)
    d = run_dir(debate_id if not minutes else f"{debate_id}-{int(minutes)}m")
    llm = LLM()
    try:
        # CPU/GPU-bound stages run in a thread so the API server's event loop stays responsive.
        words = await asyncio.to_thread(stage_transcribe, debate, d, minutes, progress)
        turns = diarize.merge_small(await asyncio.to_thread(stage_diarize, debate, d, minutes, progress))
        spk = diarize.assign_words(words, turns)
        segments = build_segments(words, spk)
        progress("locuteurs", 1.0)
        mapping = await stage_speakers(debate, d, segments, turns, llm)
        segments = speakers.apply_mapping(segments, mapping)
        claims = await stage_claims(debate, d, segments, llm, progress)
        verdicts: list[Verdict] = []
        if verify:
            verifier = Verifier(build_connectors(debate), llm, Library.load())
            verdicts = await stage_verify(debate, d, claims, verifier, strict, progress)
        events = simulate_events(segments, claims, verdicts)
        run = Run(debate=debate, segments=segments, claims=claims, verdicts=verdicts, events=events,
                  stats=stats(debate, segments, claims, verdicts, llm) | {"strict": strict, "mapping": mapping})
        (d / "run.json").write_text(run.model_dump_json())
        return run
    finally:
        await llm.aclose()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sic.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("debate_id")
    r.add_argument("--minutes", type=float, default=None)
    r.add_argument("--no-strict", action="store_true", help="allow information published after the debate")
    r.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    last = {"t": 0.0}

    def progress(stage: str, p: float) -> None:
        if time.monotonic() - last["t"] > 2 or p >= 1:
            last["t"] = time.monotonic()
            print(f"[{stage}] {p:.0%}", file=sys.stderr, flush=True)

    run = asyncio.run(process(args.debate_id, args.minutes, not args.no_strict, progress, not args.no_verify))
    print(json.dumps(run.stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
