"""HTTP API against the fixture run."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sic import config, server
from sic.models import Run

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "sample-run.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    debates = tmp_path / "debates"
    (runs / "fixture").mkdir(parents=True)
    debates.mkdir()
    shutil.copy(FIXTURE, runs / "fixture" / "run.json")
    run = Run.model_validate_json(FIXTURE.read_text())
    meta = run.debate.model_copy(update={"media": run.debate.media.model_copy(update={"file": "media/test.mp4"})})
    (debates / "fixture.json").write_text(meta.model_dump_json())
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "test.mp4").write_bytes(b"\x00" * 5000)
    monkeypatch.setattr(config, "RUNS", runs)
    monkeypatch.setattr(config, "DEBATES", debates)
    monkeypatch.setattr(config, "ROOT", tmp_path)
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
