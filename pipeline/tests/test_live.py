"""Live pipeline logic with fake ASR / embeddings / LLM (no audio models needed)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from sic import live as live_mod
from sic.models import Word
from sic.verify import Verifier

from .helpers import FakeConnector, FakeLLM, debate

SCRIPT = [
    # (cluster vector, text)
    ([0, 0, 1], "Madame Martin, vous avez la parole sur le pouvoir d'achat."),
    ([1, 0, 0], "Merci. Le chômage est à 7,4 % fin 2021, le plus bas depuis quinze ans."),
    ([1, 0, 0], "Et nous avons créé un million d'emplois pendant ces cinq années difficiles."),
    ([0, 1, 0], "C'est faux Madame Martin, la dette a explosé de six cents milliards d'euros."),
    ([0, 1, 0], "Et vous avez voté contre le bouclier tarifaire à l'Assemblée nationale."),
]


@pytest.fixture()
def fake_models(monkeypatch):
    state = {"i": 0}

    def fake_transcribe(samples, offset=0.0, names=None):
        _vec, text = SCRIPT[state["i"] % len(SCRIPT)]
        toks = text.split()
        dur = len(samples) / 16000
        return [Word(start=offset + k * dur / len(toks), end=offset + (k + 1) * dur / len(toks), text=t) for k, t in enumerate(toks)]

    class FakeEmbedder:
        def embed(self, samples):
            vec, _ = SCRIPT[state["i"] % len(SCRIPT)]
            state["i"] += 1
            v = np.array(vec, dtype=np.float32)
            return v / np.linalg.norm(v)

    monkeypatch.setattr(live_mod.asr, "transcribe", fake_transcribe)
    monkeypatch.setattr(live_mod, "Embedder", FakeEmbedder)
    return state


def _responder(messages, kw):
    system = messages[0]["content"]
    if "identifies les intervenants" in system:
        return {"content": json.dumps({"mapping": {"spk0": {"id": "m", "confidence": 0.9}, "spk1": {"id": "a", "confidence": 0.9}, "spk2": {"id": "b", "confidence": 0.9}}})}
    if "détecteur d'affirmations" in system:
        user = messages[1]["content"]
        focus = user.split("FOCUS :")[1]
        claims = []
        for line in focus.strip().splitlines():
            sid = line.split("|")[0].strip("[ ")
            text = line.split("] ", 1)[1]
            if any(k in text for k in ("7,4", "milliards", "voté")):
                claims.append({"segment_id": sid, "quote": " ".join(text.split()[:8]), "claim": text, "topic": "emploi", "kind": "chiffre", "priority": 1, "own_program": False})
        return {"content": json.dumps({"claims": claims})}
    if kw.get("tools"):
        return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "insee_get_series", "arguments": "{}"}}]}
    return {"content": json.dumps({"label": "exact", "headline": "ok", "explanation": "e", "sources": [1], "confidence": 0.9})}


@pytest.mark.asyncio
async def test_live_pipeline_emits_segments_claims_verdicts(fake_models):
    events = []

    async def emit(e):
        events.append(e)

    llm = FakeLLM(_responder)
    d = debate()
    pipe = live_mod.LivePipeline(d, llm, Verifier([FakeConnector()], llm), emit)
    t = 0.0
    for _ in range(len(SCRIPT)):
        await pipe._utterance(np.zeros(16000 * 6, dtype=np.float32), t)
        t += 6.5
    await pipe._detect(force=True)
    import asyncio

    await asyncio.gather(*list(pipe._tasks))

    types = [e.type for e in events]
    assert types.count("segment") >= len(SCRIPT)
    assert "speaker" in types
    mapping = [e for e in events if e.type == "speaker"][-1].data["mapping"]
    assert mapping == {"spk0": "m", "spk1": "a", "spk2": "b"}
    claims = [e.data for e in events if e.type == "claim"]
    assert {c["speaker"] for c in claims} <= {"a", "b"}
    assert len(claims) >= 2
    verdicts = [e.data for e in events if e.type == "verdict"]
    assert len(verdicts) == len(claims)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    # Every verdict comes after its claim.
    pos = {e.data.get("id") or e.data.get("claim_id"): i for i, e in enumerate(events) if e.type in ("claim",)}
    for i, e in enumerate(events):
        if e.type == "verdict":
            assert pos[e.data["claim_id"]] < i


def test_clock_speed():
    c = live_mod.Clock(start=100.0, speed=10.0)
    assert 100.0 <= c.now() < 101.0
