"""Speech-to-text with mlx-whisper (Apple Silicon)."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np

from sic import config
from sic.models import Word

SAMPLE_RATE = 16000

# mlx is not safe to drive from several threads at once.
_lock = threading.Lock()


def _prompt(names: list[str]) -> str:
    # Whisper uses the initial prompt as spelling context (proper names, style).
    base = "Débat politique télévisé en français."
    return f"{base} Avec {', '.join(names)}." if names else base


def _clean_words(raw: list[dict], offset: float) -> list[Word]:
    words: list[Word] = []
    for w in raw:
        text = w["word"].strip()
        if not text:
            continue
        words.append(
            Word(
                start=round(float(w["start"]) + offset, 3),
                end=round(float(w["end"]) + offset, 3),
                text=text,
                prob=round(float(w.get("probability", 1.0)), 3),
            )
        )
    return words


_REPEAT = re.compile(r"\b(\w+(?:\s+\w+){0,3})(?:\s+\1\b){3,}", re.IGNORECASE)


def is_hallucination(text: str, avg_logprob: float, no_speech_prob: float, compression_ratio: float) -> bool:
    t = text.strip()
    if not t:
        return True
    if compression_ratio > 2.6 or _REPEAT.search(t):
        return True
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return True
    low = t.lower()
    junk = ("sous-titrage", "sous-titres réalisés", "merci d'avoir regardé", "abonnez-vous")
    return any(j in low for j in junk)


def transcribe(audio: np.ndarray | str | Path, offset: float = 0.0, names: list[str] | None = None) -> list[Word]:
    """Transcribe audio (float32 mono 16 kHz array or file path). Returns timestamped words."""
    import mlx_whisper

    src = str(audio) if isinstance(audio, (str, Path)) else audio.astype(np.float32)
    with _lock:
        result = mlx_whisper.transcribe(
            src,
            path_or_hf_repo=config.ASR_MODEL,
            language="fr",
            word_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=_prompt(names or []),
            hallucination_silence_threshold=2.0,
            verbose=None,
        )
    words: list[Word] = []
    for seg in result.get("segments", []):
        if is_hallucination(
            seg.get("text", ""),
            seg.get("avg_logprob", 0.0),
            seg.get("no_speech_prob", 0.0),
            seg.get("compression_ratio", 1.0),
        ):
            continue
        words.extend(_clean_words(seg.get("words", []), offset))
    return words


def load_audio(path: str | Path, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    """Load (part of) a 16 kHz mono wav as float32."""
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate != SAMPLE_RATE or info.channels != 1:
        raise ValueError(f"{path}: expected 16 kHz mono wav, got {info.samplerate} Hz x{info.channels}")
    a = int(start * SAMPLE_RATE)
    b = None if duration is None else a + int(duration * SAMPLE_RATE)
    data, _ = sf.read(str(path), start=a, stop=b, dtype="float32")
    return data


def transcribe_long(
    path: str | Path,
    names: list[str] | None = None,
    chunk: float = 600.0,
    overlap: float = 5.0,
    on_progress=None,
) -> list[Word]:
    """Transcribe a long file in chunks (bounded memory, progress reporting).

    Chunks overlap by `overlap` seconds; words from the overlap are taken from the
    chunk where they are farther from the edge (see `merge_chunk`).
    """
    import soundfile as sf

    total = sf.info(str(path)).duration
    words: list[Word] = []
    t = 0.0
    while t < total:
        dur = min(chunk + overlap, total - t)
        audio = load_audio(path, t, dur)
        chunk_words = transcribe(audio, offset=t, names=names)
        words = merge_chunk(words, chunk_words, cut=t + overlap / 2) if t > 0 else chunk_words
        t += chunk
        if on_progress:
            on_progress(min(t, total) / total)
    return words


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.lower())


def merge_chunk(words: list[Word], chunk_words: list[Word], cut: float, tol: float = 0.05, same: float = 0.5) -> list[Word]:
    """Merge the words of an overlapping chunk into `words` (both sorted by start).

    Words before `cut` (the overlap midpoint) come from the previous chunk. From the new chunk we keep
    the words starting after the last kept word's end (minus `tol`): the boundary follows the actual
    words rather than a fixed time, so a word whose start jitters across `cut` between the two chunks
    is neither dropped nor duplicated. Identical words at ~the same time (within `same` s) are deduped.
    """
    kept = [w for w in words if w.start < cut]
    floor = kept[-1].end - tol if kept else float("-inf")
    out = list(kept)
    tail = kept[-3:]
    for w in chunk_words:
        if w.start < floor:
            continue
        key = _norm(w.text)
        if any(_norm(k.text) == key and abs(k.start - w.start) < same for k in tail):
            continue
        out.append(w)
        tail = (tail + [w])[-3:]
    return out


def ensure_wav(media: str | Path, out: str | Path) -> Path:
    """Extract 16 kHz mono wav from any media file with ffmpeg (cached).

    ffmpeg writes to a temporary file that is atomically renamed when it succeeds, so an
    interrupted extraction never leaves a truncated wav that would be reused as the cache.
    """
    import os
    import subprocess
    import tempfile

    out = Path(out)
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.stem}.", suffix=".wav.tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
             "-f", "wav", str(tmp)],
            check=True,
        )
        if tmp.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg produced an empty wav for {media}")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out
