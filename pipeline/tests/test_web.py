"""Web search connector: parsing, ranking, as_of filtering and leak guard (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from sic.sources.base import VerifyContext
from sic.sources.web import WebConnector, classify, parse_brave, parse_date, source_kind

BRAVE_HTML = """
<div class="snippet svelte-x" data-pos="0" data-type="web" data-keynav="true"><a href="https://www.insee.fr/fr/statistiques/6051733" class="l1">
<div class="title search-snippet-title" title="Au quatrième trimestre 2021, le taux de chômage diminue à 7,4 %">x</div></a>
<div class="generic-snippet"><div class="content desktop-default-regular t-primary">Feb 18, 2022 - Le taux de chômage <strong>7,4 %</strong></div></div></div>
<div class="snippet svelte-x" data-pos="1" data-type="video"><a href="https://youtube.com/watch?v=1">v</a></div>
"""


def test_parse_brave():
    r = parse_brave(BRAVE_HTML)
    assert len(r) == 1
    assert r[0]["link"].endswith("/6051733") and "7,4 %" in r[0]["content"] and r[0]["publish_date"] == "Feb 18, 2022"


def test_parse_date_and_classify():
    assert parse_date("Apr 11, 2022 — texte") == date(2022, 4, 11)
    assert parse_date("publié le 2021-03-05") == date(2021, 3, 5)
    assert parse_date("rien") is None
    assert classify("https://www.insee.fr/fr/statistiques/1") == "trusted"
    assert classify("https://www.lemonde.fr/les-decodeurs/article/x") == "factcheck"
    assert classify("https://blog.example.com/x") == "other"
    assert source_kind("https://www.assemblee-nationale.fr/dyn/15/scrutins/1") == "parliament"
    assert source_kind("https://www.legifrance.gouv.fr/loda/id/X") == "law"


@pytest.mark.asyncio
async def test_search_filters_future_and_leaks(monkeypatch):
    w = WebConnector(leak_terms=["débat de l'entre-deux-tours"])
    results = [
        {"title": "Chômage T4 2021", "link": "https://www.insee.fr/fr/statistiques/6051733", "content": "Feb 18, 2022 — 7,4 %"},
        {"title": "Chômage 2025", "link": "https://www.lefigaro.fr/x", "content": "Feb 10, 2026 — 7,9 %"},
        {"title": "Ce qu'il faut retenir du débat de l'entre-deux-tours", "link": "https://www.lemonde.fr/les-decodeurs/a", "content": "Apr 21, 2022"},
        {"title": "Blog", "link": "https://blog.example.com/p", "content": "Jan 1, 2022 — 7,4 %"},
        {"title": "Accueil", "link": "https://www.insee.fr", "content": "homepage"},
    ]

    async def fake_brave(q):
        return results

    async def fake_zai(q, d):
        return []

    monkeypatch.setattr(w, "_brave_search", fake_brave)
    monkeypatch.setattr(w, "_raw_search", fake_zai)
    r = await w.call("web_search", {"query": "chômage fin 2021"}, VerifyContext(as_of=date(2022, 4, 20)))
    urls = [s.url for s in r.sources]
    assert "https://www.lefigaro.fr/x" not in urls  # published after the debate
    assert all("lemonde" not in u for u in urls)  # leak guard
    assert urls[0] == "https://www.insee.fr/fr/statistiques/6051733"  # trusted first
    assert urls[-1] == "https://www.insee.fr"  # homepage-only links ranked last
    assert "2 résultat(s) écarté(s)" in r.content
    # Replay mode (no as_of) keeps everything.
    r2 = await w.call("web_search", {"query": "chômage fin 2021"}, VerifyContext())
    assert len(r2.sources) == 5


@pytest.mark.asyncio
async def test_tool_errors_are_returned_as_text(monkeypatch):
    w = WebConnector()

    async def boom(*a, **k):
        raise RuntimeError("réseau")

    monkeypatch.setattr(w, "search", boom)
    r = await w.call("web_search", {"query": "x"}, VerifyContext())
    assert "Erreur" in r.content and r.sources == []
