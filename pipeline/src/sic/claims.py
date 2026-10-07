"""Detect checkable factual claims in transcript windows."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from sic import config
from sic.llm import LLM
from sic.models import Claim, Debate, Segment
from sic.text import locate_quote, normalize

TOPICS = [
    "pouvoir d'achat", "emploi", "retraites", "dette et déficits", "fiscalité", "énergie", "climat", "Russie et Ukraine",
    "Europe", "immigration", "sécurité", "justice", "santé", "éducation", "institutions", "agriculture", "logement",
    "économie", "international", "société", "autre",
]

SYSTEM = """Tu es le détecteur d'affirmations de Sic, un service de fact-checking de débats politiques.
On te donne un extrait de transcription automatique (avec des erreurs possibles) : un CONTEXTE (déjà traité)
puis un FOCUS. Extrais toutes les affirmations FACTUELLES VÉRIFIABLES prononcées dans le FOCUS.

Vérifiable = peut être confirmé ou infirmé avec des données ou documents publics :
- chiffres, statistiques, montants, évolutions, classements, comparaisons (entre pays, entre périodes) ;
- votes, positions et déclarations passées d'une personne ou d'un parti ;
- contenu de lois, de traités, de programmes (« vous proposez de… », « votre programme prévoit… ») ;
- faits historiques, décisions prises, événements datés ;
- citations attribuées à quelqu'un.
À exclure : opinions, jugements de valeur, promesses ou intentions personnelles (« je ferai », « je veux »),
prédictions, questions, généralités vagues sans contenu précis, attaques sans fait précis, rhétorique.
À exclure aussi : ce qu'un candidat dit de SON PROPRE programme ou de ses propres propositions (« je propose »,
« mon projet prévoit », « mes mesures rendraient X euros ») — ce sont des promesses et des chiffrages, pas des
faits. En revanche, ce qu'un candidat affirme du programme, des votes ou des actes de son ADVERSAIRE est
vérifiable (« vous proposez de sortir du marché européen », « vous avez voté contre ») : garde-le.
Exception : quand un candidat affirme un fait vérifiable SUR sa propre proposition — son coût ou son chiffrage
(« cela rendra 12 milliards aux Français »), sa faisabilité juridique (« la France peut baisser la TVA seule »),
ou ce qu'elle n'implique pas (« mon projet ne fait pas sortir de l'UE ») — garde-le avec own_program=false.
Ignore les propos des modérateurs sauf s'ils énoncent eux-mêmes un fait chiffré.

Pour chaque affirmation :
- "segment_id" : l'id du segment du FOCUS où elle commence ;
- "quote" : copie EXACTE et courte (5 à 40 mots) du texte prononcé qui contient l'affirmation ;
- "claim" : reformulation autonome, compréhensible sans contexte, en français : remplace « je », « vous »,
  « nous », « votre quinquennat », « ce gouvernement », « l'an dernier »… par les noms et périodes exacts
  (date du débat fournie). Garde les chiffres et unités tels qu'ils ont été dits. Formule-la comme un fait
  affirmé, sans « Selon X » ni « X affirme que ». Une affirmation = un fait : sépare les phrases qui
  contiennent plusieurs faits ;
- "topic" : un thème parmi {topics} ;
- "kind" : "chiffre" | "vote" | "loi" | "historique" | "comparaison" | "citation" | "autre" ;
- "priority" : 1 (chiffre précis, vote, fait central et contestable), 2 (fait vérifiable ordinaire),
  3 (fait secondaire ou peu contestable) ;
- "own_program" : true si l'orateur parle de son propre programme, de ses propositions ou de leurs effets
  attendus (ces éléments seront écartés), sinon false.
Ne répète pas une affirmation déjà présente dans le CONTEXTE. S'il n'y a rien, renvoie une liste vide.
Réponds en JSON : {{"claims": [...]}}"""


def _fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _names(debate: Debate) -> dict[str, str]:
    return {p.id: p.name for p in debate.roster}


def _line(s: Segment, names: dict[str, str]) -> str:
    who = names.get(s.speaker or "", "Inconnu")
    return f"[{s.id} | {_fmt_time(s.start)} | {who}] {s.text}"


def _roster_text(debate: Debate) -> str:
    rows = []
    for p in debate.roster:
        role = {"candidate": "candidat", "moderator": "modérateur"}.get(p.role, "invité")
        rows.append(f"- {p.name} ({role}{', ' + p.party if p.party else ''})")
    return "\n".join(rows)


async def detect_claims(
    focus: Sequence[Segment],
    context: Sequence[Segment],
    debate: Debate,
    llm: LLM,
    model: str | None = None,
    id_prefix: str = "c",
) -> list[Claim]:
    focus = [s for s in focus if s.text.strip()]
    if not focus:
        return []
    names = _names(debate)
    user = (
        f"Débat : {debate.title}, diffusé le {debate.date.isoformat()}.\nParticipants :\n{_roster_text(debate)}\n\n"
        f"CONTEXTE :\n" + ("\n".join(_line(s, names) for s in context) or "(début)")
        + "\n\nFOCUS :\n" + "\n".join(_line(s, names) for s in focus)
    )
    data = await llm.json(
        [{"role": "system", "content": SYSTEM.format(topics=", ".join(TOPICS))}, {"role": "user", "content": user}],
        model or config.LLM_SMART,
        max_tokens=6000,
    )
    raw = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    by_id = {s.id: s for s in focus}
    ordered = list(focus)
    claims: list[Claim] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("claim") or not item.get("quote"):
            continue
        if item.get("own_program") is True:
            continue
        seg = by_id.get(str(item.get("segment_id", "")))
        quote = str(item["quote"]).strip()
        span = _locate(quote, seg, ordered)
        if span is None:
            continue  # the model invented text that was not said: drop it
        segs, start, end = span
        speaker = segs[0].speaker or "autre"
        if names and speaker not in names:
            speaker = segs[0].speaker or "autre"
        topic = item.get("topic") if item.get("topic") in TOPICS else "autre"
        kind = item.get("kind") if item.get("kind") in ("chiffre", "vote", "loi", "historique", "comparaison", "citation", "autre") else "autre"
        try:
            prio = min(3, max(1, int(item.get("priority", 2))))
        except (TypeError, ValueError):
            prio = 2
        claims.append(
            Claim(
                id=f"{id_prefix}{segs[0].id[1:]}-{len(claims)}",
                speaker=speaker,
                start=start,
                end=end,
                quote=quote,
                claim=str(item["claim"]).strip(),
                topic=topic,
                kind=kind,
                checkable=True,
                priority=prio,
                segment_ids=[s.id for s in segs],
            )
        )
    return claims


def _locate(quote: str, seg: Segment | None, ordered: list[Segment]) -> tuple[list[Segment], float, float] | None:
    """Find the quote in the hinted segment (and its neighbours), else anywhere in the focus."""
    candidates: list[list[Segment]] = []
    if seg is not None:
        i = ordered.index(seg)
        candidates.append(ordered[max(0, i - 1) : i + 3])
    candidates.append(ordered)
    for group in candidates:
        words = []
        owners = []
        for s in group:
            for w in s.words:
                words.append(w)
                owners.append(s)
        if not words:
            # No word timings (tests / text-only input): fall back to substring search.
            for s in group:
                if normalize(quote)[:40] in normalize(s.text):
                    return [s], s.start, s.end
            continue
        span = locate_quote(quote, words)
        if span:
            i, j = span
            segs = []
            for s in owners[i : j + 1]:
                if s not in segs:
                    segs.append(s)
            return segs, words[i].start, words[j].end
    return None


def similar(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"\w+", normalize(a))), set(re.findall(r"\w+", normalize(b)))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe(claims: list[Claim], threshold: float = 0.8) -> list[Claim]:
    out: list[Claim] = []
    for c in sorted(claims, key=lambda c: c.start):
        dup = next(
            (o for o in out if o.speaker == c.speaker and (abs(o.start - c.start) < 30 or similar(o.claim, c.claim) >= threshold)
             and similar(o.claim, c.claim) >= 0.55),
            None,
        )
        if dup is None:
            out.append(c)
    return out


def windows(segments: list[Segment], focus_s: float = 150.0, context_s: float = 60.0) -> list[tuple[list[Segment], list[Segment]]]:
    """Split a transcript into (focus, context) windows for offline processing."""
    out = []
    i = 0
    n = len(segments)
    while i < n:
        start = segments[i].start
        j = i
        while j < n and segments[j].end - start <= focus_s:
            j += 1
        j = max(j, i + 1)
        ctx = [s for s in segments[:i] if s.start >= start - context_s]
        out.append((segments[i:j], ctx))
        i = j
    return out


async def detect_all(segments: list[Segment], debate: Debate, llm: LLM, model: str | None = None, on_progress=None) -> list[Claim]:
    wins = windows(segments)
    done = 0

    async def run(focus, ctx):
        nonlocal done
        try:
            return await detect_claims(focus, ctx, debate, llm, model)
        finally:
            done += 1
            if on_progress:
                on_progress(done / len(wins))

    results = await asyncio.gather(*(run(f, c) for f, c in wins))
    claims = [c for r in results for c in r]
    return dedupe(claims)
