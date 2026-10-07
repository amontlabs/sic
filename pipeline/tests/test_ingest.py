"""Ingest (yt-dlp download selection), ffmpeg wav cache and long-file ASR chunk merging — all with fakes."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sic import asr, config, ingest
from sic.models import Debate, Media, Word

from .helpers import FakeLLM

# -- yt-dlp audio selection -----------------------------------------------------------------------


def test_find_audio_ignores_leftovers(tmp_path):
    assert ingest.find_audio(tmp_path / "missing") is None
    for name in ("audio.m4a.part", "audio.m4a.ytdl", "audio.info.json", "audio.webm.tmp", "audio.f251.webm.part", "audio.m4a.part-Frag3"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "audio.mp3").write_bytes(b"")  # empty: not a finished download
    assert ingest.find_audio(tmp_path) is None
    (tmp_path / "audio.opus").write_bytes(b"x")  # ends with "s": missed by the old glob
    assert ingest.find_audio(tmp_path) == tmp_path / "audio.opus"


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(config, "DEBATES", tmp_path / "debates")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "debates").mkdir()
    return tmp_path


def _fakes(monkeypatch, downloads: list, ext: str = "m4a"):
    def fake_run(cmd, **kw):
        assert cmd[0] == "yt-dlp"
        downloads.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1]).parent
        (out / f"audio.{ext}").write_bytes(b"\x00" * 10)
        (out / "audio.info.json").write_text(json.dumps({"title": "Titre", "channel": "Chaîne", "upload_date": "20240315", "duration": 42}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    wavs: list = []

    def fake_wav(src, out):
        wavs.append(Path(src))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\x00")
        return Path(out)

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(asr, "ensure_wav", fake_wav)
    monkeypatch.setattr(asr, "load_audio", lambda *a, **k: np.zeros(10, dtype=np.float32))
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: [Word(start=0, end=1, text="Bonsoir")])
    return wavs


def _llm():
    roster = {"title": "Débat", "broadcaster": "TV", "roster": [{"id": "a", "name": "A", "role": "candidate"}, {"id": "a", "name": "A2", "role": "guest"}]}
    return FakeLLM(lambda m, kw: {"content": json.dumps(roster)})


@pytest.mark.asyncio
async def test_ingest_redownloads_after_interrupted_download(paths, monkeypatch):
    raw = paths / "data" / "raw" / "yt-x"
    raw.mkdir(parents=True)
    (raw / "audio.m4a.part").write_bytes(b"\x00" * 3)  # interrupted download
    (raw / "audio.info.json").write_text("{}")
    downloads: list = []
    wavs = _fakes(monkeypatch, downloads)
    debate = await ingest.ingest_youtube("abcdefghijk", "yt-x", llm=_llm())
    assert len(downloads) == 1
    assert wavs == [raw / "audio.m4a"]  # never the .part file
    assert debate.title == "Débat" and debate.date == date(2024, 3, 15) and [p.id for p in debate.roster] == ["a"]
    assert (config.DEBATES / "yt-x.json").exists()


@pytest.mark.asyncio
async def test_ingest_reuses_completed_download(paths, monkeypatch):
    raw = paths / "data" / "raw" / "yt-y"
    raw.mkdir(parents=True)
    (raw / "audio.opus").write_bytes(b"\x00" * 3)
    (raw / "audio.info.json").write_text(json.dumps({"title": "T", "upload_date": "20240101"}))
    downloads: list = []
    wavs = _fakes(monkeypatch, downloads)
    await ingest.ingest_youtube("abcdefghijk", "yt-y", llm=_llm())
    assert downloads == [] and wavs == [raw / "audio.opus"]


@pytest.mark.asyncio
async def test_ingest_fails_cleanly_when_nothing_downloaded(paths, monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="did not produce"):
        await ingest.ingest_youtube("abcdefghijk", "yt-z", llm=_llm())


def test_audio_path(paths, monkeypatch):
    d = Debate(id="d", title="t", date=date(2022, 1, 1), roster=[], media=Media(file="data/raw/v.mp4"))
    with pytest.raises(FileNotFoundError):
        ingest.audio_path(d)  # media file missing: no ffmpeg call
    (paths / "data" / "raw").mkdir(parents=True)
    (paths / "data" / "raw" / "v.mp4").write_bytes(b"x")
    monkeypatch.setattr(asr, "ensure_wav", lambda src, out: Path(out))
    assert ingest.audio_path(d) == paths / "cache" / "audio" / "d.wav"


# -- ffmpeg wav cache -----------------------------------------------------------------------------


def test_ensure_wav_interrupted_leaves_no_truncated_cache(tmp_path, monkeypatch):
    out = tmp_path / "audio" / "x.wav"
    calls: list = []

    def interrupted(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF-truncated")
        raise subprocess.CalledProcessError(255, cmd)

    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(subprocess.CalledProcessError):
        asr.ensure_wav(tmp_path / "in.mp4", out)
    assert not out.exists()
    assert list(out.parent.iterdir()) == []  # temp file cleaned up
    assert Path(calls[0][-1]) != out and "-f" in calls[0]

    def ok(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF-complete")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", ok)
    assert asr.ensure_wav(tmp_path / "in.mp4", out) == out
    assert out.read_bytes() == b"RIFF-complete" and list(out.parent.iterdir()) == [out]
    asr.ensure_wav(tmp_path / "in.mp4", out)  # cached: no new ffmpeg call
    assert len(calls) == 2


def test_ensure_wav_empty_output_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    with pytest.raises(RuntimeError):
        asr.ensure_wav(tmp_path / "in.mp4", tmp_path / "x.wav")
    assert list(tmp_path.iterdir()) == []


# -- long-file chunk merge ------------------------------------------------------------------------


def _w(text: str, start: float, end: float) -> Word:
    return Word(start=start, end=end, text=text)


def test_merge_chunk_jitter_across_cut():
    cut = 11.0
    prev = [_w("a", 9.0, 9.5), _w("b", 10.4, 10.9), _w("c", 10.95, 11.0), _w("d", 11.02, 11.4)]
    # Same words seen by the next chunk with timestamp jitter: "c" now starts after the cut
    # (old code: duplicated), "d" now starts before it (old code: dropped).
    new = [_w("b", 10.42, 10.9), _w("c,", 11.01, 11.05), _w("d", 10.99, 11.4), _w("e", 11.5, 12.0)]
    out = asr.merge_chunk(prev, new, cut)
    assert [w.text for w in out] == ["a", "b", "c", "d", "e"]


def test_merge_chunk_no_previous_words_before_cut():
    out = asr.merge_chunk([_w("x", 11.5, 12.0)], [_w("y", 10.2, 10.5), _w("x", 11.6, 12.0)], cut=11.0)
    assert [w.text for w in out] == ["y", "x"]


def test_transcribe_long_merges_overlapping_chunks(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    sf.write(str(wav), np.zeros(int(18 * asr.SAMPLE_RATE), dtype=np.float32), asr.SAMPLE_RATE)
    # chunk=10, overlap=2: chunks [0, 12) and [10, 18); cut at 11.
    script = {
        0.0: [_w("un", 1.0, 1.5), _w("deux", 9.0, 9.5), _w("trois", 10.5, 10.98), _w("quatre", 11.03, 11.5)],
        10.0: [_w("deux", 10.0, 10.2), _w("trois", 10.52, 10.98), _w("quatre", 10.97, 11.5), _w("cinq", 12.0, 12.4), _w("six", 17.0, 17.5)],
    }
    seen: list = []

    def fake_transcribe(audio, offset=0.0, names=None):
        seen.append((offset, round(len(audio) / asr.SAMPLE_RATE, 2)))
        return script[offset]

    monkeypatch.setattr(asr, "transcribe", fake_transcribe)
    progress: list = []
    words = asr.transcribe_long(wav, chunk=10.0, overlap=2.0, on_progress=progress.append)
    assert seen == [(0.0, 12.0), (10.0, 8.0)]
    assert [w.text for w in words] == ["un", "deux", "trois", "quatre", "cinq", "six"]
    assert progress[-1] == 1.0
