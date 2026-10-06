"""Compare a processed run with published fact-checks of the same debate (gold set).

    uv run python -m sic.evaluate 2022-macron-lepen

Metrics:
- detection recall: share of gold claims that Sic also extracted;
- verdict agreement on matched claims (exact label, and 3 buckets: correct / problem / unverifiable);
- critical errors: Sic says correct where fact-checkers say faux, or faux where they say correct.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from sic import config
from sic.claims import similar
from sic.library import tokens
from sic.llm import LLM
from sic.models import Claim, Run, Verdict

BUCKET = {"exact": "correct", "plutot_exact": "correct", "imprecis": "problem", "trompeur": "problem", "faux": "problem", "inverifiable": "unverifiable"}


class GoldItem(BaseModel):
    id: str
    speaker: str
    quote: str = ""
    claim: str
    topic: str | None = None
    verdict: str
    explanation: str = ""
    refs: list[dict] = Field(default_factory=list)


MATCH_SYSTEM = """Tu compares des affirmations extraites automatiquement d'un débat avec une affirmation vérifiée
par des journalistes. Indique laquelle des affirmations candidates porte sur le MÊME fait (même sujet, même
chiffre ou même assertion — une formulation différente ou plus large reste un match si le fait central est
le même). S'il n'y en a aucune, réponds null.
Réponds en JSON : {"match": "<id ou null>", "why": "court"}"""


def load_gold(debate_id: str) -> list[GoldItem]:
    p = config.DATA / "gold" / f"{debate_id}.jsonl"
    return [GoldItem.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]


def _candidates(g: GoldItem, claims: list[Claim], k: int = 12) -> list[Claim]:
    q = set(tokens(f"{g.claim} {g.quote}"))
    scored = []
    for c in claims:
        if c.speaker != g.speaker:
            continue
        d = set(tokens(f"{c.claim} {c.quote}"))
        overlap = len(q & d) / (len(q) or 1)
        scored.append((overlap + similar(g.claim, c.claim), c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]


async def match(gold: list[GoldItem], claims: list[Claim], llm: LLM) -> dict[str, str | None]:
    async def one(g: GoldItem) -> tuple[str, str | None]:
        cands = _candidates(g, claims)
        if not cands:
            return g.id, None
        listing = "\n".join(f"- {c.id} : {c.claim} (citation : « {c.quote} »)" for c in cands)
        data = await llm.json(
            [{"role": "system", "content": MATCH_SYSTEM},
             {"role": "user", "content": f"Affirmation vérifiée : {g.claim}\nCitation : « {g.quote} »\n\nCandidates :\n{listing}"}],
            config.LLM_SMART, max_tokens=400,
        )
        mid = data.get("match") if isinstance(data, dict) else None
        return g.id, mid if any(c.id == mid for c in cands) else None

    return dict(await asyncio.gather(*(one(g) for g in gold)))


def score(gold: list[GoldItem], run: Run, matches: dict[str, str | None]) -> dict:
    vmap: dict[str, Verdict] = {v.claim_id: v for v in run.verdicts}
    cmap = {c.id: c for c in run.claims}
    rows = []
    for g in gold:
        cid = matches.get(g.id)
        v = vmap.get(cid) if cid else None
        rows.append({
            "gold_id": g.id, "speaker": g.speaker, "gold_claim": g.claim, "gold_verdict": g.verdict,
            "claim_id": cid, "sic_claim": cmap[cid].claim if cid else None,
            "sic_verdict": v.label if v else None, "sic_headline": v.headline if v else None,
            "method": v.method if v else None, "latency_s": v.latency_s if v else None,
            "outlets": [r.get("outlet") for r in g.refs],
        })
    matched = [r for r in rows if r["claim_id"]]
    judged = [r for r in matched if r["sic_verdict"]]
    exact = sum(r["sic_verdict"] == r["gold_verdict"] for r in judged)
    bucket = sum(BUCKET[r["sic_verdict"]] == BUCKET.get(r["gold_verdict"], "?") for r in judged)
    decided = [r for r in judged if r["sic_verdict"] != "inverifiable"]
    bucket_decided = sum(BUCKET[r["sic_verdict"]] == BUCKET.get(r["gold_verdict"], "?") for r in decided)
    critical = [
        r for r in judged
        if (BUCKET[r["sic_verdict"]] == "correct" and r["gold_verdict"] == "faux")
        or (r["sic_verdict"] == "faux" and BUCKET.get(r["gold_verdict"]) == "correct")
    ]
    lat = sorted(v.latency_s for v in run.verdicts if v.latency_s)
    return {
        "gold": len(gold),
        "detected": len(matched),
        "recall": round(len(matched) / max(1, len(gold)), 3),
        "judged": len(judged),
        "label_agreement": round(exact / max(1, len(judged)), 3),
        "bucket_agreement": round(bucket / max(1, len(judged)), 3),
        "bucket_agreement_when_decided": round(bucket_decided / max(1, len(decided)), 3),
        "sic_unverifiable_on_matched": sum(r["sic_verdict"] == "inverifiable" for r in judged),
        "critical_errors": len(critical),
        "critical": critical,
        "sic_claims": len(run.claims),
        "sic_verdicts": len(run.verdicts),
        "sic_by_label": dict(Counter(v.label for v in run.verdicts)),
        "sic_methods": dict(Counter(v.method for v in run.verdicts)),
        "latency_p50": lat[len(lat) // 2] if lat else None,
        "latency_p90": lat[int(len(lat) * 0.9)] if lat else None,
        "rows": rows,
    }


def report_md(debate_id: str, s: dict) -> str:
    L = [
        f"# Évaluation — {debate_id}",
        "",
        "Comparaison avec les vérifications publiées par les rédactions (voir `data/gold/`).",
        "",
        "| Mesure | Valeur |",
        "|---|---|",
        f"| Affirmations vérifiées par les rédactions | {s['gold']} |",
        f"| … détectées par Sic (rappel) | {s['detected']} ({s['recall']:.0%}) |",
        f"| Verdict identique | {s['label_agreement']:.0%} |",
        f"| Même catégorie (juste / problème / invérifiable) | {s['bucket_agreement']:.0%} |",
        f"| Même catégorie, hors « invérifiable » de Sic | {s['bucket_agreement_when_decided']:.0%} |",
        f"| « Invérifiable » chez Sic sur ces affirmations | {s['sic_unverifiable_on_matched']} |",
        f"| Erreurs critiques (juste ↔ faux) | {s['critical_errors']} |",
        f"| Affirmations extraites par Sic (total) | {s['sic_claims']} |",
        f"| Latence de vérification p50 / p90 | {s['latency_p50']} s / {s['latency_p90']} s |",
        "",
        "## Détail",
        "",
        "| Orateur | Affirmation (rédactions) | Verdict rédactions | Verdict Sic | Titre Sic |",
        "|---|---|---|---|---|",
    ]
    for r in s["rows"]:
        L.append(
            f"| {r['speaker']} | {r['gold_claim'][:110]} | {r['gold_verdict']} | {r['sic_verdict'] or ('non détectée' if not r['claim_id'] else '—')} | {(r['sic_headline'] or '')[:80]} |"
        )
    return "\n".join(L) + "\n"


async def evaluate(debate_id: str, run_path: Path | None = None) -> dict:
    run = Run.model_validate_json((run_path or config.RUNS / debate_id / "run.json").read_text())
    gold = load_gold(debate_id)
    llm = LLM()
    try:
        matches = await match(gold, run.claims, llm)
    finally:
        await llm.aclose()
    s = score(gold, run, matches)
    out = config.DATA / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{debate_id}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1))
    (out / f"{debate_id}.md").write_text(report_md(debate_id, s))
    return s


def main() -> None:
    ap = argparse.ArgumentParser(prog="sic.evaluate")
    ap.add_argument("debate_id")
    ap.add_argument("--run", type=Path, default=None)
    a = ap.parse_args()
    s = asyncio.run(evaluate(a.debate_id, a.run))
    print(json.dumps({k: v for k, v in s.items() if k not in ("rows", "critical")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
