"""HTTP API against the fixture run."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sic import asr, config, ingest, server
from sic.models import Event, Run

from .helpers import FakeLLM

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "sample-run.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    debates = tmp_path / "debates"
    (runs / "fixture").mkdir(parents=True)
    debates.mkdir()
    shutil.copy(FIXTURE, runs / "fixture" / "run.json")
    run = Run.model_validate_json(FIXTURE.read_text())
    meta = run.debate.model_copy(update={"media": run.debate.media.model_copy(update={"file": "data/raw/test.mp4"})})
    (debates / "fixture.json").write_text(meta.model_dump_json())
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "test.mp4").write_bytes(b"\x00" * 5000)
    monkeypatch.setattr(config, "RUNS", runs)
    monkeypatch.setattr(config, "DEBATES", debates)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "CACHE", tmp_path / "data" / "cache")
    monkeypatch.delenv("SIC_OPERATOR_TOKEN", raising=False)
    server.runs._runs.clear()
    server.jobs.clear()
    return TestClient(server.app)


def test_list_and_get(client):
    lst = client.get("/api/debates").json()
    assert lst[0]["id"] == "fixture" and lst[0]["status"] == "ready"
    assert lst[0]["counts"]["claims"] == 7 and lst[0]["counts"]["by_label"]["faux"] == 2
    run = client.get("/api/debates/fixture").json()
    assert len(run["segments"]) == 10 and run["segments"][0]["words"] == []
    assert len(run["events"]) == 23
    assert "events" not in client.get("/api/debates/fixture?events=0").json()


def test_not_found_and_bad_id(client):
    assert client.get("/api/debates/nope").status_code == 404
    assert client.get("/api/debates/..%2Fetc").status_code in (400, 404)


def _sse(client, url):
    events = []
    with client.stream("GET", url) as r:
        assert r.status_code == 200
        typ = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                typ = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((typ, json.loads(line.split(":", 1)[1])))
                if events[-1][1].get("data", {}).get("state") == "end":
                    break
    return events


def test_live_replay_paced_and_complete(client):
    ev = _sse(client, "/api/debates/fixture/live?from=0&speed=100")
    types = [t for t, _ in ev]
    assert types[0] == "status" and ev[-1][1]["data"]["state"] == "end"
    payload = [d for t, d in ev if t != "status"]
    assert len(payload) == 23
    ats = [d["at"] for d in payload]
    assert ats == sorted(ats)


def test_live_from_offset_skips_old_segments(client):
    ev = _sse(client, "/api/debates/fixture/live?from=60&speed=100")
    segs = [d for t, d in ev if t == "segment"]
    assert all(d["t"] >= 60 - 120 for d in segs)
    verdicts = [d for t, d in ev if t == "verdict"]
    assert len(verdicts) == 6  # verdicts are always replayed (catch-up)


def test_review_marks_verdict(client):
    r = client.post("/api/debates/fixture/verdicts/c0002", json={"label": "plutot_exact", "reviewed": True})
    assert r.status_code == 200 and r.json()["label"] == "plutot_exact" and r.json()["reviewed"] is True
    run = client.get("/api/debates/fixture").json()
    v = next(v for v in run["verdicts"] if v["claim_id"] == "c0002")
    assert v["reviewed"] is True
    ev = next(e for e in run["events"] if e["type"] == "verdict" and e["data"]["claim_id"] == "c0002")
    assert ev["data"]["label"] == "plutot_exact"
    assert client.post("/api/debates/fixture/verdicts/zzz", json={"reviewed": True}).status_code == 404
    assert client.post("/api/debates/fixture/verdicts/c0002", json={"label": "mensonge"}).status_code == 422


def test_media_range(client):
    r = client.get("/media/fixture", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert client.get("/media/zzz").status_code == 404


def test_analyze_validates_url(client, monkeypatch):
    called = {}

    async def fake_job(debate_id, video_id):
        called["id"] = (debate_id, video_id)

    monkeypatch.setattr(server, "_analyze_job", fake_job)
    assert client.post("/api/analyze", json={"url": "https://example.com/x"}).status_code == 400
    r = client.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=elgTG9oDiJY&t=10"})
    assert r.json() == {"debate_id": "yt-elgTG9oDiJY"}
    assert called["id"] == ("yt-elgTG9oDiJY", "elgTG9oDiJY")


def test_review_minutes_run_saves_to_its_own_dir(client):
    # A `--minutes 20` run lives in RUNS/<id>-20m/ but its debate.id is <id>.
    full = config.RUNS / "fixture" / "run.json"
    short = config.RUNS / "fixture-20m" / "run.json"
    short.parent.mkdir()
    shutil.copy(full, short)
    before = full.read_bytes()
    r = client.post("/api/debates/fixture-20m/verdicts/c0002", json={"label": "plutot_exact"})
    assert r.status_code == 200
    assert full.read_bytes() == before  # the full run is untouched
    saved = Run.model_validate_json(short.read_text())
    assert saved.debate.id == "fixture"
    assert next(v for v in saved.verdicts if v.claim_id == "c0002").label == "plutot_exact"
    assert client.get("/api/debates/fixture-20m").json()["verdicts"][1]["reviewed"] is True


def test_review_product_rules(client):
    run = Run.model_validate_json((config.RUNS / "fixture" / "run.json").read_text())
    v = next(v for v in run.verdicts if v.claim_id == "c0002")
    v.sources = []
    (config.RUNS / "fixture" / "run.json").write_text(run.model_dump_json())
    for label in ("faux", "trompeur", "imprecis"):
        r = client.post("/api/debates/fixture/verdicts/c0002", json={"label": label})
        assert r.status_code == 422 and "source" in r.json()["detail"]
    assert client.post("/api/debates/fixture/verdicts/c0002", json={"label": "inverifiable"}).status_code == 200
    for text in ("C'est un MENSONGE", "le candidat est menteur", "Menteuse !"):
        assert client.post("/api/debates/fixture/verdicts/c0003", json={"headline": text}).status_code == 422
        assert client.post("/api/debates/fixture/verdicts/c0003", json={"explanation": text}).status_code == 422
    r = client.post("/api/debates/fixture/verdicts/c0003", json={"headline": "Chiffre inexact"})
    assert r.status_code == 200 and r.json()["label"] == "faux"  # has a source: allowed


def test_write_routes_require_operator(client, monkeypatch):
    calls = []

    async def fake_job(debate_id, video_id):
        calls.append(debate_id)

    monkeypatch.setattr(server, "_analyze_job", fake_job)
    yt = {"url": "https://youtu.be/elgTG9oDiJY"}
    # No token configured: localhost only.
    remote = TestClient(server.app, client=("203.0.113.7", 5000))
    assert remote.post("/api/debates/fixture/verdicts/c0002", json={"reviewed": True}).status_code == 403
    assert remote.post("/api/analyze", json=yt).status_code == 403
    assert remote.get("/api/debates").status_code == 200  # reads stay public
    local = TestClient(server.app, client=("127.0.0.1", 5000))
    assert local.post("/api/debates/fixture/verdicts/c0002", json={"reviewed": True}).status_code == 200
    # Token configured: header required, from anywhere.
    monkeypatch.setenv("SIC_OPERATOR_TOKEN", "s3cret")
    assert local.post("/api/debates/fixture/verdicts/c0002", json={"reviewed": True}).status_code == 401
    bad = {"X-Sic-Operator": "nope"}
    assert remote.post("/api/analyze", json=yt, headers=bad).status_code == 401
    ok = {"X-Sic-Operator": "s3cret"}
    assert remote.post("/api/debates/fixture/verdicts/c0002", json={"reviewed": True}, headers=ok).status_code == 200
    assert remote.post("/api/analyze", json=yt, headers=ok).json() == {"debate_id": "yt-elgTG9oDiJY"}
    assert calls == ["yt-elgTG9oDiJY"]


def test_cors_restricted(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-sic-operator"}
    r = client.options("/api/analyze", headers={"Origin": "http://localhost:5173", **pre})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-sic-operator" in r.headers["access-control-allow-headers"].lower()
    r = client.options("/api/analyze", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/debates", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("SIC_CORS_ORIGINS", "https://sic.vote, https://www.sic.vote")
    assert server.cors_origins() == ["https://sic.vote", "https://www.sic.vote"]
    monkeypatch.delenv("SIC_CORS_ORIGINS")
    assert server.cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]


def _set_media(file):
    meta = json.loads((config.DEBATES / "fixture.json").read_text())
    meta["media"]["file"] = file
    (config.DEBATES / "fixture.json").write_text(json.dumps(meta))


def test_media_only_serves_raw_audio_video(client):
    (config.ROOT / ".env").write_text("ZAI_API_KEY=secret")
    (config.ROOT / "data" / "raw" / "notes.txt").write_text("x")
    (config.ROOT / "data" / "raw" / "clip.mp3").write_bytes(b"\x01" * 10)
    for bad in ("../../.env", ".env", "data/debates/fixture.json", "data/raw/../../.env", "data/raw/notes.txt",
                "data/raw/missing.mp4", "data/raw", "/etc/passwd"):
        _set_media(bad)
        assert client.get("/media/fixture").status_code == 404, bad
    _set_media("data/raw/clip.mp3")
    r = client.get("/media/fixture")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"


def test_media_symlink_escape(client):
    (config.ROOT / ".env").write_text("ZAI_API_KEY=secret")
    (config.ROOT / "data" / "raw" / "evil.mp4").symlink_to(config.ROOT / ".env")
    _set_media("data/raw/evil.mp4")
    assert client.get("/media/fixture").status_code == 404


def test_list_processing_first_then_date_desc(client):
    for i, d in (("old", "2020-01-01"), ("new", "2024-01-01"), ("mid", "2022-06-01")):
        meta = json.loads((config.DEBATES / "fixture.json").read_text()) | {"id": i, "date": d}
        (config.DEBATES / f"{i}.json").write_text(json.dumps(meta))
    server.jobs["old"] = {"status": "processing", "progress": 0.3, "stage": "transcription"}
    ids = [s["id"] for s in client.get("/api/debates").json()]
    assert ids == ["old", "new", "mid", "fixture"]  # fixture: 2022-04-20


# -- pipeline-mode live ---------------------------------------------------------------------------


def test_live_pipeline_validation(client):
    assert client.get("/api/debates/nope/live?mode=pipeline").status_code == 404
    r = client.get("/api/debates/fixture/live?mode=pipeline")  # media file is not a real video, no wav cached
    assert r.status_code == 404 and r.json()["detail"] == "no audio for this debate"
    _set_media(None)
    assert client.get("/api/debates/fixture/live?mode=pipeline").status_code == 404


class _Req:
    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_live_pipeline_stream_cleanup(tmp_path, monkeypatch):
    import sic.library
    import sic.live
    import sic.llm
    import sic.sources

    state: dict = {}

    class FakePipe:
        def __init__(self, debate, llm, verifier, emit) -> None:
            self.verifier, self._emit, self.clock = verifier, emit, sic.live.Clock()
            state["pipe"] = self

        async def run_file(self, wav, start=0.0):
            for i, kind in enumerate(("status", "segment")):
                await self._emit(Event(seq=i + 1, type=kind, t=start, at=start, data={"state": "start"} if kind == "status" else {}))
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["runner_cancelled"] = True
                raise

        async def aclose(self) -> None:
            state["pipe_closed"] = True

    llm = FakeLLM(lambda m, kw: {"content": "{}"})

    async def llm_close():
        state["llm_closed"] = "pipe_closed" in state and "runner_cancelled" in state  # order: pipe, runner, llm

    llm.aclose = llm_close
    monkeypatch.setattr(sic.live, "LivePipeline", FakePipe)
    monkeypatch.setattr(sic.llm, "LLM", lambda: llm)
    monkeypatch.setattr(sic.sources, "build_connectors", lambda debate: [])
    monkeypatch.setattr(sic.library.Library, "load", classmethod(lambda cls: None))
    run = Run.model_validate_json(FIXTURE.read_text())

    gen = server._pipeline_stream(run.debate, tmp_path / "x.wav", 0.0, _Req())
    got = [await gen.__anext__(), await gen.__anext__()]
    assert [g["event"] for g in got] == ["status", "segment"]
    await gen.aclose()  # client went away
    assert state == {"pipe": state["pipe"], "pipe_closed": True, "runner_cancelled": True, "llm_closed": True}
    assert state["pipe"].verifier.model == config.LLM_SMART


# -- analyze does not block the event loop --------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_keeps_event_loop_responsive(tmp_path, monkeypatch):
    import sic.pipeline

    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(config, "DEBATES", tmp_path / "debates")
    monkeypatch.setattr(config, "RUNS", tmp_path / "runs")
    monkeypatch.delenv("SIC_OPERATOR_TOKEN", raising=False)
    (tmp_path / "debates").mkdir()
    server.jobs.clear()
    server.runs._runs.clear()
    block = 0.3

    def fake_yt_dlp(*args):
        time.sleep(block)
        out = Path(args[args.index("-o") + 1]).parent
        (out / "audio.webm").write_bytes(b"\x00" * 100)
        (out / "audio.info.json").write_text(json.dumps({"title": "T", "upload_date": "20240102", "duration": 60}))

    def fake_wav(src, out):
        time.sleep(block)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\x00")
        return Path(out)

    monkeypatch.setattr(ingest, "_yt_dlp", fake_yt_dlp)
    monkeypatch.setattr(asr, "ensure_wav", fake_wav)
    monkeypatch.setattr(asr, "load_audio", lambda *a, **k: time.sleep(block))
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: time.sleep(block) or [])
    roster = {"title": "T", "roster": [{"id": "a", "name": "A", "role": "candidate"}]}
    monkeypatch.setattr(ingest, "LLM", lambda: FakeLLM(lambda m, kw: {"content": json.dumps(roster)}))

    async def fake_process(debate_id, strict=True, progress=None):
        await asyncio.to_thread(time.sleep, block)

    monkeypatch.setattr(sic.pipeline, "process", fake_process)

    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as c:
        post = asyncio.create_task(c.post("/api/analyze", json={"url": "https://youtu.be/abcdefghijk"}))
        await asyncio.sleep(0.05)
        latencies = []
        while not post.done():
            t0 = time.monotonic()
            r = await c.get("/api/debates")
            latencies.append(time.monotonic() - t0)
            assert r.status_code == 200
            await asyncio.sleep(0.05)
        assert (await post).json() == {"debate_id": "yt-abcdefghijk"}
    done.set()
    await tick
    assert server.jobs["yt-abcdefghijk"]["status"] == "ready", server.jobs
    assert len(latencies) >= 5 and max(latencies) < block / 2, latencies
    assert max(gaps) < block / 2, max(gaps)
