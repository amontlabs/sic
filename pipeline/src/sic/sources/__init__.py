"""Connector registry."""

from __future__ import annotations

import importlib
import logging
import re
import unicodedata

from sic.models import Debate, Person
from sic.sources.base import Connector

log = logging.getLogger(__name__)

_CONNECTORS = [
    ("sic.sources.insee", "InseeConnector"),
    ("sic.sources.eurostat", "EurostatConnector"),
    ("sic.sources.assemblee", "AssembleeConnector"),
]

_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_CIVILITY = re.compile(r"^(?:madame|monsieur|mme|m\.|mr\.?)\s+(.+)$", re.IGNORECASE)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^0-9a-z]+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()


def surname(p: Person) -> str:
    """"Marine Le Pen" -> "Le Pen" (confirmed by a "Madame Le Pen" alias when there is one)."""
    parts = p.name.split(maxsplit=1)
    guess = parts[1] if len(parts) == 2 else p.name
    for alias in p.aliases:
        m = _CIVILITY.match(alias.strip())
        if m and m.group(1)[:1].isupper() and _fold(p.name).endswith(_fold(m.group(1))):
            return m.group(1)
    return guess


def leak_terms(debate: Debate | None) -> list[str]:
    """Phrases that identify pages about the debate itself (excluded in strict live mode).

    Matching is accent/case/punctuation-insensitive (see `sic.sources.web.LeakGuard`); `*` stands
    for up to 3 words ("débat * Macron" matches "débat télévisé entre Emmanuel Macron"). This
    deliberately over-blocks: a pre-debate page announcing "le débat face à Marine Le Pen" is
    dropped too. Generic uses such as "grand débat national" or "débat parlementaire" are exempt.
    """
    if debate is None:
        return []
    d = debate.date
    month = _MONTHS[d.month - 1]
    day = "1er" if d.day == 1 else str(d.day)
    weekday = _WEEKDAYS[d.weekday()]
    terms = [
        "débat de l'entre-deux-tours", "débat d'entre-deux-tours", "le débat d'hier soir", "débat d'hier soir",
        "hier soir", "lors du débat", "pendant le débat", "au cours du débat", "à l'issue du débat", "face-à-face",
        f"débat du {day} {month}", f"débat du {weekday} {day} {month}", f"débat de {weekday}",
        f"{day} {month} {d.year}", f"{weekday} {day} {month}", d.strftime("%d/%m/%Y"),
    ]
    if "entre deux tours" in _fold(debate.title):  # the debate *is* the entre-deux-tours one
        terms.append("entre-deux-tours")
    for p in debate.roster:
        if p.role != "candidate":
            continue
        s = surname(p)
        terms += [f"débat * {s}", f"{s} * débat télévisé", f"face-à-face * {s}", f"duel télévisé * {s}"]
    out: list[str] = []
    for t in terms:
        if t not in out:
            out.append(t)
    return out


def build_connectors(debate: Debate | None = None, web: bool = True) -> list[Connector]:
    out: list[Connector] = []
    for mod, cls in _CONNECTORS:
        try:
            out.append(getattr(importlib.import_module(mod), cls)())
        except Exception as e:  # a missing/broken connector must not take the pipeline down
            log.warning("connector %s unavailable: %s", cls, e)
    terms = leak_terms(debate)
    try:
        from sic.sources.wikipedia import WikipediaConnector

        out.append(WikipediaConnector(leak_terms=terms))
    except Exception as e:
        log.warning("connector WikipediaConnector unavailable: %s", e)
    if web:
        from sic.sources.web import WebConnector

        out.append(WebConnector(leak_terms=terms))
    return out
