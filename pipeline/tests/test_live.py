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
    await pipe.drain()
    await pipe.aclose()

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


@pytest.mark.asyncio
async def test_claims_held_until_voice_named_then_reemitted_on_rename(fake_models):
    """No claim is ever published under an anonymous voice; a later rename re-publishes and re-verifies."""
    import asyncio

    events = []

    async def emit(e):
        events.append(e)

    naming = {"n": 0}

    def responder(messages, kw):
        system = messages[0]["content"]
        if "identifies les intervenants" in system:
            naming["n"] += 1
            if naming["n"] == 1:  # first attempt: the model is unsure about everyone
                return {"content": json.dumps({"mapping": {}})}
            if naming["n"] == 2:  # then names spk1 (wrongly) as b
                return {"content": json.dumps({"mapping": {"spk0": {"id": "m", "confidence": 0.9}, "spk1": {"id": "b", "confidence": 0.9}}})}
            return {"content": json.dumps({"mapping": {"spk0": {"id": "m", "confidence": 0.9}, "spk1": {"id": "a", "confidence": 0.9}}})}
        return _responder(messages, kw)

    llm = FakeLLM(responder)
    pipe = live_mod.LivePipeline(debate(), llm, Verifier([FakeConnector()], llm), emit)
    await pipe._utterance(np.zeros(16000 * 6, dtype=np.float32), 0.0)  # moderator
    await pipe._utterance(np.zeros(16000 * 6, dtype=np.float32), 6.5)  # spk1: "Le chômage est à 7,4 %"
    await pipe._detect(force=True)
    await pipe.drain()
    # first naming returned nothing usable -> claim is held, nothing published
    assert not [e for e in events if e.type == "claim"] or all(e.data["speaker"] in ("a", "b") for e in events if e.type == "claim")
    await pipe._name()  # names spk1 = b -> held claim published as b
    await pipe.drain()
    first = [e.data for e in events if e.type == "claim"]
    assert first and first[-1]["speaker"] == "b" and first[-1]["cluster"] == "spk1"
    await pipe._name()  # corrected: spk1 = a -> claim re-published as a and re-verified
    await pipe.drain()
    claims = [e.data for e in events if e.type == "claim"]
    assert claims[-1]["speaker"] == "a" and claims[-1].get("revised") is True and claims[-1]["id"] == first[-1]["id"]
    verdict_events = [e for e in events if e.type == "verdict" and e.data["claim_id"] == first[-1]["id"]]
    assert len(verdict_events) >= 1 and verdict_events[-1].seq > [e for e in events if e.type == "claim"][-1].seq
    await pipe.aclose()
    assert not pipe._tasks and not pipe._workers
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_aclose_cancels_background_work(fake_models):
    import asyncio

    started = asyncio.Event()

    def slow(messages, kw):
        raise AssertionError("should be cancelled before answering")

    class SlowLLM(FakeLLM):
        async def chat(self, messages, model=None, **kw):
            started.set()
            await asyncio.sleep(3600)

    async def emit(e):
        pass

    pipe = live_mod.LivePipeline(debate(), SlowLLM(slow), None, emit)
    for i in range(3):
        await pipe._utterance(np.zeros(16000 * 10, dtype=np.float32), i * 10.5)
    await pipe._detect(force=True)
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(pipe.aclose(), 2)
    assert not pipe._tasks
