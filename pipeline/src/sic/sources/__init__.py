"""Connector registry."""

from __future__ import annotations

import importlib
import logging

from sic.models import Debate
from sic.sources.base import Connector

log = logging.getLogger(__name__)

_CONNECTORS = [
    ("sic.sources.insee", "InseeConnector"),
    ("sic.sources.eurostat", "EurostatConnector"),
    ("sic.sources.assemblee", "AssembleeConnector"),
]


def leak_terms(debate: Debate | None) -> list[str]:
    """Phrases that identify articles about the debate itself (excluded in strict live mode)."""
    if debate is None:
        return []
    terms = ["débat de l'entre-deux-tours", "débat d'entre-deux-tours", "débat de l’entre-deux-tours", "le débat d'hier soir"]
    d = debate.date
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    terms.append(f"débat du {d.day} {months[d.month - 1]}")
    terms.append(f"débat du mercredi {d.day} {months[d.month - 1]}")
    return terms


def build_connectors(debate: Debate | None = None, web: bool = True) -> list[Connector]:
    out: list[Connector] = []
    for mod, cls in _CONNECTORS:
        try:
            out.append(getattr(importlib.import_module(mod), cls)())
        except Exception as e:  # a missing/broken connector must not take the pipeline down
            log.warning("connector %s unavailable: %s", cls, e)
    if web:
        from sic.sources.web import WebConnector

        out.append(WebConnector(leak_terms=leak_terms(debate)))
    return out
