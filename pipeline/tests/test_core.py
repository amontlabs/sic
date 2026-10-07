"""Core pipeline logic: text building, claim parsing, verification loop, guards, library, events."""

from __future__ import annotations

import json
from datetime import date

import pytest

from sic import diarize
from sic.claims import dedupe, detect_claims, similar, windows
from sic.library import Library, LibraryEntry, LibraryRef
from sic.llm import parse_json
from sic.models import Claim, Source, Verdict, Word
from sic.pipeline import simulate_events
from sic.sources.base import VerifyContext
from sic.speakers import _cluster_digest, apply_mapping, name_speakers
from sic.text import build_segments, join_words, locate_quote, normalize
from sic.verify import Verifier, guard

from .helpers import FakeConnector, FakeLLM, debate, seg

# -- text ---------------------------------------------------------------------------------------


def test_join_words_french_elisions_and_punctuation():
    words = ["D", "'un", "prix", ",", "l", "'énergie", "est", "à", "4", "%", "."]
    assert join_words(words) == "D'un prix, l'énergie est à 4%."


def test_build_segments_splits_on_speaker_and_sentence():
    ws, spk = [], []
    t = 0.0
    for sp, text in [("a", "Le chômage baisse depuis cinq ans."), ("a", "Il est à sept pour cent."), ("b", "C'est faux.")]:
        for tok in text.split():
            ws.append(Word(start=t, end=t + 0.5, text=tok))
            spk.append(sp)
            t += 0.5
    segs = build_segments(ws, spk, min_sentence=1.0)
    assert [s.speaker for s in segs] == ["a", "a", "b"]
    assert segs[0].text == "Le chômage baisse depuis cinq ans."
    assert segs[2].start == pytest.approx(ws[-2].start)
    assert segs[0].id == "s00000" and segs[2].id == "s00002"


def test_build_segments_splits_long_pauses():
    ws = [Word(start=0, end=0.5, text="Bonjour"), Word(start=3.0, end=3.5, text="madame")]
    assert len(build_segments(ws, ["a", "a"])) == 2


def test_locate_quote_fuzzy():
    s = seg(0, "a", "Nous avons créé un million deux cent mille emplois depuis 2017 , c'est historique", 10.0, 8.0)
    span = locate_quote("créé un million deux cent mille emplois", s.words)
    assert span is not None
    i, j = span
    assert s.words[i].text == "créé" and s.words[j].text == "emplois"
    assert locate_quote("phrase qui n'a jamais été prononcée ici", s.words) is None


def test_normalize_accents():
    assert normalize("Énergie, Électricité !") == "energie, electricite"


# -- diarization helpers -----------------------------------------------------------------------


def test_assign_words_overlap_and_gap_fill():
    turns = [diarize.Turn(0, 2, "spk0"), diarize.Turn(2.2, 5, "spk1")]
    words = [Word(start=0.1, end=0.5, text="a"), Word(start=2.12, end=2.19, text="b"), Word(start=3, end=3.5, text="c"), Word(start=9, end=9.5, text="d")]
    assert diarize.assign_words(words, turns) == ["spk0", "spk1", "spk1", "spk1"]


def test_online_tracker_creates_and_joins_clusters():
    import numpy as np

    tr = diarize.OnlineSpeakerTracker(threshold=0.8)
    a = np.array([1.0, 0, 0], dtype=np.float32)
    b = np.array([0, 1.0, 0], dtype=np.float32)
    assert tr.assign(a, 3) == "spk0"
    assert tr.assign(b, 3) == "spk1"
    assert tr.assign(np.array([0.95, 0.05, 0], dtype=np.float32) / 0.951, 3) == "spk0"


# -- llm helpers --------------------------------------------------------------------------------


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Voici : {"a": [1, 2]} fin') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        parse_json("pas de json")


# -- claims -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_claims_locates_quotes_and_drops_inventions():
    d = debate()
    focus = [
        seg(1, "a", "Le chômage a baissé de deux points depuis 2017 et c'est un record", 100.0, 6.0),
        seg(2, "b", "Je proposerai une baisse de la TVA sur l'énergie", 106.0, 4.0),
    ]
    reply = {"claims": [
        {"segment_id": "s00001", "quote": "Le chômage a baissé de deux points depuis 2017", "claim": "Le taux de chômage a baissé de deux points depuis 2017.", "topic": "emploi", "kind": "chiffre", "priority": 1, "own_program": False},
        {"segment_id": "s00002", "quote": "baisse de la TVA sur l'énergie", "claim": "Bruno Petit propose une baisse de la TVA.", "topic": "énergie", "kind": "loi", "priority": 2, "own_program": True},
        {"segment_id": "s00001", "quote": "une phrase inventée par le modèle qui n'existe pas", "claim": "x", "topic": "emploi"},
    ]}
    llm = FakeLLM(lambda m, kw: {"content": json.dumps(reply), "tool_calls": []})
    claims = await detect_claims(focus, [], d, llm)
    assert len(claims) == 1
    c = claims[0]
    assert c.speaker == "a" and c.kind == "chiffre" and c.priority == 1
    assert c.start == pytest.approx(100.0) and c.end < 106.0
    assert c.segment_ids == ["s00001"]
    prompt = llm.calls[0][0][1]["content"]
    assert "Alice Martin" in prompt and "FOCUS" in prompt and "2022-04-20" in prompt


@pytest.mark.asyncio
async def test_detect_claims_handles_garbage():
    llm = FakeLLM(lambda m, kw: {"content": json.dumps({"claims": "nope"}), "tool_calls": []})
    assert await detect_claims([seg(1, "a", "bonjour à tous", 0)], [], debate(), llm) == []
    assert await detect_claims([], [], debate(), llm) == []


def test_windows_cover_everything_once():
    segs = [seg(i, "a", f"phrase numéro {i}", i * 10.0, 9.0) for i in range(60)]
    wins = windows(segs, focus_s=100, context_s=30)
    flat = [s.id for f, _ in wins for s in f]
    assert flat == [s.id for s in segs]
    assert all(c.start >= f[0].start - 30 for f, ctx in wins for c in ctx)


def test_dedupe_and_similarity():
    mk = lambda i, t, start: Claim(id=f"c{i}", speaker="a", start=start, end=start + 3, quote=t, claim=t, topic="emploi")
    cl = [mk(1, "Le chômage est à 7,4 % fin 2021", 10), mk(2, "Le chômage est à 7,4 % fin 2021 !", 500), mk(3, "La dette atteint 3000 milliards", 20)]
    assert similar(cl[0].claim, cl[1].claim) > 0.8
    assert [c.id for c in dedupe(cl)] == ["c1", "c3"]


# -- speakers -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_name_speakers_maps_and_rejects_low_confidence():
    segs = [
        seg(0, "spk2", "Madame Martin, vous avez la parole", 0),
        seg(1, "spk0", "Merci, le chômage a baissé", 5),
        seg(2, "spk2", "Monsieur Petit, votre réponse", 10),
        seg(3, "spk1", "C'est faux madame Martin", 15),
    ]
    digest = _cluster_digest(segs)
    assert "Madame Martin, vous avez la parole" in digest["spk0"]["before"][0]
    reply = {"mapping": {"spk0": {"id": "a", "confidence": 0.9}, "spk1": {"id": "b", "confidence": 0.8}, "spk2": {"id": "m", "confidence": 0.2}}}
    llm = FakeLLM(lambda m, kw: {"content": json.dumps(reply), "tool_calls": []})
    mapping = await name_speakers(segs, debate(), llm)
    assert mapping == {"spk0": "a", "spk1": "b", "spk2": "autre"}
    assert [s.speaker for s in apply_mapping(segs, mapping)] == ["autre", "a", "autre", "b"]


# -- verification -------------------------------------------------------------------------------


def _claim() -> Claim:
    return Claim(id="c1", speaker="a", start=10, end=15, quote="le chômage est à 7,4 %", claim="Le taux de chômage était de 7,4 % fin 2021.", topic="emploi", kind="chiffre")


@pytest.mark.asyncio
async def test_verifier_tool_loop_and_citations():
    conn = FakeConnector()
    step = {"n": 0}

    def responder(messages, kw):
        step["n"] += 1
        if step["n"] == 1:
            assert kw.get("tools"), "tools must be offered on the first step"
            return {"content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "insee_get_series", "arguments": '{"idbank": "001688527"}'}}]}
        tool_msg = messages[-1]
        assert tool_msg["role"] == "tool" and "[S1]" in tool_msg["content"]
        return {"content": json.dumps({"label": "exact", "headline": "7,4 % au T4 2021", "explanation": "Selon l'INSEE.", "sources": ["S1"], "confidence": 0.9}), "tool_calls": []}

    v = Verifier([conn], FakeLLM(responder), library=None)
    ctx = VerifyContext(as_of=date(2022, 4, 20), debate=debate())
    verdict = await v.verify(_claim(), ctx)
    assert verdict.label == "exact" and verdict.method == "data"
    assert verdict.sources[0].publisher == "INSEE"
    assert conn.calls[0][2].as_of == date(2022, 4, 20)
    assert verdict.latency_s is not None


@pytest.mark.asyncio
async def test_verifier_forces_answer_after_max_steps():
    conn = FakeConnector()

    def responder(messages, kw):
        if kw.get("json_mode"):
            return {"content": json.dumps({"label": "imprecis", "headline": "h", "explanation": "e", "sources": [1], "confidence": 0.7}), "tool_calls": []}
        return {"content": "", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "insee_get_series", "arguments": "{}"}}]}

    v = Verifier([conn], FakeLLM(responder), max_steps=2)
    verdict = await v.verify(_claim(), VerifyContext())
    assert verdict.label == "imprecis"
    assert len(conn.calls) >= 2


@pytest.mark.asyncio
async def test_verifier_survives_llm_failure():
    def boom(messages, kw):
        raise RuntimeError("down")

    verdict = await Verifier([FakeConnector()], FakeLLM(boom)).verify(_claim(), VerifyContext())
    assert verdict.label == "inverifiable" and verdict.method == "none"


@pytest.mark.asyncio
async def test_verifier_unknown_tool_and_bad_args_do_not_crash():
    seq = iter([
        {"content": "", "tool_calls": [{"id": "1", "function": {"name": "nope", "arguments": "{}"}}, {"id": "2", "function": {"name": "insee_get_series", "arguments": "{bad"}}]},
        {"content": json.dumps({"label": "inverifiable", "headline": "h", "explanation": "e", "sources": [], "confidence": 0.2})},
    ])
    v = Verifier([FakeConnector()], FakeLLM(lambda m, kw: next(seq)))
    verdict = await v.verify(_claim(), VerifyContext())
    assert verdict.label == "inverifiable"


def test_guard_rules():
    base = Verdict(claim_id="c", label="faux", headline="h", explanation="e", sources=[], confidence=0.9)
    assert guard(base).label == "inverifiable"  # no negative verdict without a source
    src = [Source(kind="official_data", title="t", url="u")]
    assert guard(base.model_copy(update={"sources": src})).label == "faux"
    assert guard(base.model_copy(update={"sources": src, "confidence": 0.2})).label == "inverifiable"
    assert guard(base.model_copy(update={"sources": src, "headline": ""})).headline == "e"


@pytest.mark.asyncio
async def test_library_match_path():
    lib = Library([
        LibraryEntry(id="L1", speakers=["a"], claim="Le taux de chômage est au plus bas depuis 15 ans", variants=["chômage plus bas depuis quinze ans"],
                     verdict="imprecis", explanation="Plus bas depuis 2008.", refs=[LibraryRef(outlet="AFP Factuel", url="https://factuel.afp.com/x", published="2022-03-01")],
                     checked_at="2022-03-01"),
    ])
    claim = Claim(id="c2", speaker="a", start=0, end=3, quote="le chômage est au plus bas depuis quinze ans", claim="Le taux de chômage est au plus bas depuis 15 ans.", topic="emploi")
    llm = FakeLLM(lambda m, kw: {"content": json.dumps({"match": "L1", "label": "imprecis", "headline": "Plus bas depuis 2008", "explanation": "e", "confidence": 0.8})})
    v = Verifier([FakeConnector()], llm, library=lib)
    verdict = await v.verify(claim, VerifyContext(as_of=date(2022, 4, 20)))
    assert verdict.method == "library" and verdict.library_id == "L1" and verdict.label == "imprecis"
    assert verdict.sources[0].kind == "factcheck" and verdict.reviewed is False


def test_library_respects_as_of_and_speaker():
    e = LibraryEntry(id="L1", speakers=["a"], claim="dette publique 3000 milliards", verdict="exact", explanation="x", checked_at="2022-05-01")
    lib = Library([e])
    assert lib.search("dette publique 3000 milliards") and lib.search("dette publique 3000 milliards", as_of=date(2022, 6, 1))
    assert lib.search("dette publique 3000 milliards", as_of=date(2022, 4, 20)) == []
    assert lib.search("dette publique 3000 milliards", speaker="b") == []


# -- events -------------------------------------------------------------------------------------


def test_simulate_events_order_and_timing():
    segs = [seg(0, "a", "Le chômage est à 7,4 %", 0, 4)]
    c = Claim(id="c1", speaker="a", start=0, end=4, quote="Le chômage est à 7,4 %", claim="x", topic="emploi")
    v = Verdict(claim_id="c1", label="exact", headline="h", explanation="e", latency_s=10.0)
    ev = simulate_events(segs, [c], [v])
    assert [e.type for e in ev] == ["segment", "claim", "verdict"]
    assert ev[0].at < ev[1].at < ev[2].at
    assert ev[2].at == pytest.approx(4 + 2 + 3 + 10)
    assert [e.seq for e in ev] == [1, 2, 3]


# -- evaluation ---------------------------------------------------------------------------------


def test_evaluation_scoring():
    from sic.evaluate import GoldItem, score
    from sic.models import Run

    d = debate()
    claims = [Claim(id=f"c{i}", speaker="a", start=i, end=i + 1, quote="q", claim=f"claim {i}", topic="emploi") for i in range(3)]
    verdicts = [
        Verdict(claim_id="c0", label="exact", headline="h", explanation="e"),
        Verdict(claim_id="c1", label="faux", headline="h", explanation="e"),
        Verdict(claim_id="c2", label="inverifiable", headline="h", explanation="e"),
    ]
    run = Run(debate=d, claims=claims, verdicts=verdicts)
    gold = [
        GoldItem(id="g0", speaker="a", claim="x", verdict="plutot_exact"),  # same bucket
        GoldItem(id="g1", speaker="a", claim="y", verdict="exact"),  # critical: Sic says faux
        GoldItem(id="g2", speaker="a", claim="z", verdict="faux"),  # Sic unverifiable
        GoldItem(id="g3", speaker="a", claim="w", verdict="faux"),  # not detected
    ]
    s = score(gold, run, {"g0": "c0", "g1": "c1", "g2": "c2", "g3": None})
    assert s["recall"] == 0.75 and s["judged"] == 3
    assert s["bucket_agreement"] == round(1 / 3, 3)
    assert s["bucket_agreement_when_decided"] == 0.5
    assert s["critical_errors"] == 1 and s["sic_unverifiable_on_matched"] == 1


def test_guard_dedupes_sources():
    s = Source(kind="parliament", title="Scrutin 1", url="https://an.fr/1")
    v = Verdict(claim_id="c", label="faux", headline="h", explanation="e", sources=[s, s, Source(kind="web", title="x", url="https://b")], confidence=0.9)
    assert [x.url for x in guard(v).sources] == ["https://an.fr/1", "https://b"]
