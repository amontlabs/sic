"""Name anonymous speaker clusters from context (who is addressed, what they say).

No biometric matching: clusters only exist within one broadcast, and names are
inferred from the conversation (moderators' introductions, "Madame Le Pen, ...").
"""

from __future__ import annotations

from collections import defaultdict

from sic import config
from sic.llm import LLM
from sic.models import Debate, Segment

UNKNOWN = "autre"

SYSTEM = """Tu identifies les intervenants d'un débat télévisé à partir de la transcription.
On te donne des groupes de voix anonymes (spk0, spk1...) détectés automatiquement, avec des extraits
de ce que chaque voix dit et de ce qui est dit juste avant qu'elle parle (souvent un modérateur qui
donne la parole : « Madame Le Pen, à vous »).
Associe chaque groupe à un participant de la liste, ou à "autre" (voix off, public, invité non listé)
si tu n'es pas sûr. Plusieurs groupes peuvent correspondre à la même personne.
Indices : les modérateurs posent des questions, distribuent la parole, annoncent les thèmes et les temps
de parole ; les candidats défendent leur bilan/programme et s'interpellent (« vous », « Monsieur Macron »).
Une personne ne s'appelle pas elle-même « Madame X » : si un extrait d'une voix dit « Madame Le Pen »,
cette voix n'est probablement pas Marine Le Pen.
Réponds en JSON : {"mapping": {"spk0": {"id": "<id participant ou autre>", "confidence": 0.0-1.0, "why": "court"}}}"""


def _cluster_digest(segments: list[Segment], max_samples: int = 8) -> dict[str, dict]:
    by: dict[str, list[Segment]] = defaultdict(list)
    for s in segments:
        if s.speaker:
            by[s.speaker].append(s)
    digest: dict[str, dict] = {}
    for spk, segs in by.items():
        dur = sum(s.end - s.start for s in segs)
        # Longest turns are the most informative.
        samples = sorted(segs, key=lambda s: -(s.end - s.start))[:max_samples]
        samples.sort(key=lambda s: s.start)
        digest[spk] = {"duration": dur, "n": len(segs), "samples": [s.text[:280] for s in samples]}
    # What is said right before each cluster starts speaking (addressing cues).
    cues: dict[str, list[str]] = defaultdict(list)
    for prev, cur in zip(segments, segments[1:]):
        if cur.speaker and prev.speaker != cur.speaker and len(cues[cur.speaker]) < 8:
            tail = prev.text[-160:]
            if any(w in tail.lower() for w in ("madame", "monsieur", "vous", "parole", "à vous", "réponse")):
                cues[cur.speaker].append(tail)
    for spk in digest:
        digest[spk]["before"] = cues.get(spk, [])
    return digest


async def name_speakers(segments: list[Segment], debate: Debate, llm: LLM) -> dict[str, str]:
    """Return {cluster_id: roster_id | "autre"}."""
    digest = _cluster_digest(segments)
    if not digest:
        return {}
    roster = "\n".join(
        f"- id={p.id} : {p.name} ({'candidat' if p.role == 'candidate' else 'modérateur' if p.role == 'moderator' else 'invité'}"
        f"{', ' + p.party if p.party else ''}) ; on l'appelle : {', '.join(p.aliases) or p.name}"
        for p in debate.roster
    )
    parts = []
    for spk, d in sorted(digest.items(), key=lambda kv: -kv[1]["duration"]):
        parts.append(
            f"## {spk} — {d['duration'] / 60:.1f} min de parole, {d['n']} segments\n"
            + "Extraits :\n" + "\n".join(f"  « {t} »" for t in d["samples"])
            + ("\nDit juste avant que cette voix parle :\n" + "\n".join(f"  « …{t} »" for t in d["before"]) if d["before"] else "")
        )
    user = f"Débat : {debate.title} ({debate.date}).\nParticipants :\n{roster}\n\nGroupes de voix :\n\n" + "\n\n".join(parts)
    data = await llm.json(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], config.LLM_SMART, max_tokens=2000
    )
    valid = {p.id for p in debate.roster}
    out: dict[str, str] = {}
    for spk in digest:
        m = (data.get("mapping") or {}).get(spk) or {}
        pid = m.get("id") if isinstance(m, dict) else m
        conf = float(m.get("confidence", 0.0)) if isinstance(m, dict) else 0.5
        out[spk] = pid if pid in valid and conf >= 0.4 else UNKNOWN
    return out


def apply_mapping(segments: list[Segment], mapping: dict[str, str]) -> list[Segment]:
    return [s.model_copy(update={"speaker": mapping.get(s.speaker or "", s.speaker)}) for s in segments]
