"""Pre-checked claim library: checks prepared before a debate, matched instantly during it."""

from __future__ import annotations

import json
import re
from datetime import date as Date
from pathlib import Path

from pydantic import BaseModel, Field
from rank_bm25 import BM25Plus

from sic import config
from sic.text import normalize

_STOP = set(
    "le la les de des du un une et en à a au aux que qui qu ne pas plus pour par sur dans est sont ce cette ces il elle ils "
    "elles on nous vous je j l d s n y se sa son ses leur leurs avec mais ou donc or ni car été être avoir fait".split()
)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w%,.]+", normalize(text)) if t not in _STOP and len(t) > 1]


class LibraryRef(BaseModel):
    outlet: str | None = None
    url: str | None = None
    published: str | None = None
    label: str | None = None


class LibraryEntry(BaseModel):
    id: str
    speakers: list[str] = Field(default_factory=list)
    claim: str
    variants: list[str] = Field(default_factory=list)
    topic: str | None = None
    verdict: str
    explanation: str
    key_facts: list[str] = Field(default_factory=list)
    refs: list[LibraryRef] = Field(default_factory=list)
    primary_sources: list[str] = Field(default_factory=list)
    checked_at: str | None = None
    reviewed: bool = False  # set True once a Sic editor has validated the entry

    def checked_date(self) -> Date | None:
        try:
            return Date.fromisoformat(self.checked_at) if self.checked_at else None
        except ValueError:
            return None


class Library:
    def __init__(self, entries: list[LibraryEntry]) -> None:
        self.entries = entries
        self._docs = [tokens(" ".join([e.claim, *e.variants, " ".join(e.key_facts)])) for e in entries]
        self._bm25 = BM25Plus(self._docs) if entries else None
        self._sets = [set(d) for d in self._docs]

    @classmethod
    def load(cls, paths: list[Path] | None = None) -> Library:
        paths = paths if paths is not None else sorted((config.DATA / "library").glob("*.jsonl"))
        entries: list[LibraryEntry] = []
        for p in paths:
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(LibraryEntry.model_validate_json(line))
                    except ValueError:
                        continue
        return cls(entries)

    def search(
        self, text: str, speaker: str | None = None, as_of: Date | None = None, k: int = 5, min_coverage: float = 0.25
    ) -> list[tuple[LibraryEntry, float]]:
        """Rank entries with BM25+, return (entry, coverage) where coverage = share of query terms found."""
        if not self._bm25:
            return []
        q = tokens(text)
        if not q:
            return []
        qs = set(q)
        scores = self._bm25.get_scores(q)
        ranked = sorted(range(len(self.entries)), key=lambda i: -scores[i])
        out: list[tuple[LibraryEntry, float]] = []
        for i in ranked:
            e = self.entries[i]
            coverage = len(qs & self._sets[i]) / len(qs)
            if coverage < min_coverage:
                continue
            d = e.checked_date()
            if as_of and (d is None or d >= as_of):
                continue  # strict live simulation: only checks published before the debate
            if speaker and e.speakers and speaker not in e.speakers:
                continue
            out.append((e, round(coverage, 3)))
            if len(out) >= k:
                break
        return out


def entry_text(e: LibraryEntry) -> str:
    refs = "; ".join(f"{r.outlet} ({r.published})" for r in e.refs if r.outlet)
    facts = "\n".join(f"  - {f}" for f in e.key_facts)
    return f"[{e.id}] {e.claim}\n  verdict: {e.verdict} — {e.explanation}\n{facts}\n  vérifié par : {refs}"
