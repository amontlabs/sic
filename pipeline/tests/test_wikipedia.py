"""Wikipedia connector: search parsing, revision-at-date lookup, HTML stripping, focus excerpts,
permalinks, caching and error reporting (offline, recorded fixtures) + one live API test."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from sic.models import Debate, Person
from sic.sources import build_connectors, leak_terms
from sic.sources.base import VerifyContext
from sic.sources.wikipedia import API, USER_AGENT, Block, WikiApi, WikipediaConnector, focus_excerpt, html_to_blocks

FIX = Path(__file__).parent / "fixtures"
AS_OF = date(2022, 4, 20)


def fixture(name: str) -> dict:
    return json.loads((FIX / f"wikipedia_{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def wiki(tmp_path: Path) -> WikipediaConnector:
    return WikipediaConnector(api=WikiApi(tmp_path, min_interval=0, retries=2, backoff=0))


def mock_taxe_carbone() -> tuple[respx.Route, respx.Route]:
    rev = respx.get(API, params={"action": "query", "prop": "revisions"}).mock(
        return_value=httpx.Response(200, json=fixture("revisions_taxe_carbone_2022-04-20"))
    )
    parse = respx.get(API, params={"action": "parse", "oldid": "192754475"}).mock(
        return_value=httpx.Response(200, json=fixture("parse_192754475"))
    )
    return rev, parse


# ---------------------------------------------------------------------------------- search


@pytest.mark.asyncio
@respx.mock
async def test_search_parsing(wiki):
    route = respx.get(API, params={"list": "search"}).mock(return_value=httpx.Response(200, json=fixture("search_taxe_carbone")))
    r = await wiki.call("wikipedia_search", {"query": "taxe carbone"}, VerifyContext())
    req = route.calls.last.request
    assert req.headers["user-agent"] == USER_AGENT
    assert req.url.params["srsearch"] == "taxe carbone" and req.url.params["srlimit"] == "5"
    assert req.url.params["format"] == "json" and req.url.params["formatversion"] == "2"
    assert "[1] Taxe carbone — " in r.content and "Taxe carbone en France" in r.content
    assert "<span" not in r.content and "searchmatch" not in r.content
    assert r.sources == []  # search results are not citable: read the article


@pytest.mark.asyncio
@respx.mock
async def test_search_strict_mode_hides_snippets_and_later_titles(wiki):
    data = fixture("search_taxe_carbone")
    data["query"]["search"] += [
        {"ns": 0, "title": "Élection présidentielle française de 2027", "snippet": "x"},
        {"ns": 0, "title": "Débat de l'entre-deux-tours de l'élection présidentielle de 2022", "snippet": "x"},
    ]
    respx.get(API, params={"list": "search"}).mock(return_value=httpx.Response(200, json=data))
    wiki.leak = WikipediaConnector(leak_terms=["débat de l'entre-deux-tours"]).leak
    r = await wiki.call("wikipedia_search", {"query": "taxe carbone", "limit": 50}, VerifyContext(as_of=AS_OF))
    assert "Taxe carbone en France" in r.content
    assert "2027" not in r.content.split("\n", 1)[1] and "Débat de l'entre" not in r.content
    assert "2 titre(s) écarté(s)" in r.content
    assert "searchmatch" not in r.content and "environnementale" not in r.content  # current-version snippets hidden


# ---------------------------------------------------------------------------------- read


@pytest.mark.asyncio
@respx.mock
async def test_read_as_of_uses_revision_at_date_and_permalink(wiki):
    rev, parse = mock_taxe_carbone()
    r = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France", "focus": "montant taxe carbone 2018 tonne"}, VerifyContext(as_of=AS_OF))
    p = rev.calls.last.request.url.params
    assert p["titles"] == "Taxe carbone en France" and p["rvlimit"] == "1" and p["rvdir"] == "older"
    assert p["rvstart"] == "2022-04-20T00:00:00Z" and p["rvprop"] == "ids|timestamp"
    pp = parse.calls.last.request.url.params
    assert pp["oldid"] == "192754475" and pp["prop"] == "text"
    [src] = r.sources
    assert src.kind == "web" and src.publisher == "Wikipédia"
    assert src.url == "https://fr.wikipedia.org/w/index.php?oldid=192754475"
    assert src.title == "Taxe carbone en France (version du 10 avril 2022)" and src.date == "2022-04-10"
    assert src.excerpt and len(src.excerpt) <= 300 and src.excerpt.startswith("La taxe carbone")
    assert "oldid=192754475" in r.content and "antérieure au 20 avril 2022" in r.content
    body = r.content.split("\n\n", 1)[1]
    assert len(body) <= 3500
    assert "2018-2021 | 44,6 | 53,5" in body  # the rate table, kept as rows
    assert "## Modalités > Montant" in body
    assert "Notes et références" not in body and "consulté le" not in body


@pytest.mark.asyncio
@respx.mock
async def test_read_current_revision_and_redirect(wiki):
    rev = respx.get(API, params={"action": "query", "prop": "revisions"}).mock(
        return_value=httpx.Response(200, json=fixture("revisions_bouclier_current"))
    )
    respx.get(API, params={"action": "parse", "oldid": "238522944"}).mock(
        return_value=httpx.Response(200, json={"parse": {"title": "Droit de la consommation", "revid": 238522944,
                                                         "text": "<div><p>Le <b>droit de la consommation</b> protège.</p></div>"}})
    )
    r = await wiki.call("wikipedia_read", {"title": "https://fr.wikipedia.org/wiki/Bouclier_tarifaire#Histoire"}, VerifyContext())
    p = rev.calls.last.request.url.params
    assert p["titles"] == "Bouclier tarifaire" and "rvstart" not in p and p["redirects"] == "1"
    assert "redirigé depuis « Bouclier tarifaire »" in r.content and "version actuelle" in r.content
    assert r.sources[0].title == "Droit de la consommation (version du 10 août 2026)"
    assert "protège" in r.content


@pytest.mark.asyncio
@respx.mock
async def test_read_drops_paragraphs_about_the_debate(wiki):
    respx.get(API, params={"action": "query"}).mock(return_value=httpx.Response(200, json=fixture("revisions_taxe_carbone_2022-04-20")))
    html = ("<div><p>Le premier tour a lieu le 10 avril 2022.</p>"
            "<p>Lors du débat d’entre-deux-tours, Marine Le Pen affirme que la taxe…</p></div>")
    respx.get(API, params={"action": "parse"}).mock(return_value=httpx.Response(200, json={"parse": {"text": html}}))
    wiki.leak = WikipediaConnector(leak_terms=leak_terms(_debate())).leak
    r = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, VerifyContext(as_of=AS_OF))
    assert "premier tour" in r.content and "Marine Le Pen affirme" not in r.content
    assert "1 paragraphe(s) écarté(s)" in r.content
    r2 = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, VerifyContext())
    assert "Marine Le Pen affirme" in r2.content  # replay mode keeps everything


@pytest.mark.asyncio
@respx.mock
async def test_missing_article_and_not_yet_created(wiki):
    respx.get(API, params={"titles": "Zzzz inexistant"}).mock(return_value=httpx.Response(200, json=fixture("revisions_missing")))
    r = await wiki.call("wikipedia_read", {"title": "Zzzz inexistant"}, VerifyContext(as_of=AS_OF))
    assert "introuvable" in r.content and r.sources == []
    later = {"query": {"pages": [{"pageid": 1, "ns": 0, "title": "Bouclier tarifaire (2022)"}]}}
    respx.get(API, params={"titles": "Bouclier tarifaire (2022)"}).mock(return_value=httpx.Response(200, json=later))
    r = await wiki.call("wikipedia_read", {"title": "Bouclier tarifaire (2022)"}, VerifyContext(as_of=AS_OF))
    assert "n'existait pas encore avant le 20 avril 2022" in r.content and r.sources == []


@pytest.mark.asyncio
@respx.mock
async def test_errors_are_returned_as_text(wiki):
    respx.get(API, params={"action": "query", "prop": "revisions"}).mock(return_value=httpx.Response(200, json=fixture("revisions_taxe_carbone_2022-04-20")))
    respx.get(API, params={"action": "parse"}).mock(return_value=httpx.Response(200, json=fixture("parse_error")))
    r = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, VerifyContext(as_of=AS_OF))
    assert r.content.startswith("Erreur wikipedia_read:") and "nosuchrevid" in r.content and r.sources == []
    respx.get(API, params={"list": "search"}).mock(side_effect=httpx.ConnectError("down"))
    r = await wiki.call("wikipedia_search", {"query": "x"}, VerifyContext())
    assert r.content.startswith("Erreur wikipedia_search:") and "indisponible" in r.content
    r = await wiki.call("wikipedia_read", {"title": "  "}, VerifyContext())
    assert "manquant" in r.content
    r = await wiki.call("wikipedia_nope", {}, VerifyContext())
    assert "inconnu" in r.content


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429_and_5xx(wiki):
    route = respx.get(API, params={"list": "search"}).mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "1"}),
        httpx.Response(503),
        httpx.Response(200, json=fixture("search_taxe_carbone")),
    ])
    r = await wiki.call("wikipedia_search", {"query": "taxe carbone"}, VerifyContext())
    assert route.call_count == 3 and "Taxe carbone en France" in r.content


@pytest.mark.asyncio
@respx.mock
async def test_cache_hits_atomic_writes_and_corrupt_entries(tmp_path):
    api = WikiApi(tmp_path, min_interval=0, retries=0, backoff=0)
    wiki = WikipediaConnector(api=api)
    rev, parse = mock_taxe_carbone()
    ctx = VerifyContext(as_of=AS_OF)
    await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, ctx)
    await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, ctx)
    assert rev.call_count == 1 and parse.call_count == 1  # revision-pinned answers never expire
    files = list(tmp_path.iterdir())
    assert len(files) == 2 and not [f for f in files if f.name.endswith(".tmp")]
    for f in files:
        f.write_text("{truncated", encoding="utf-8")  # e.g. a crash mid-write with a non-atomic writer
    r = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France"}, ctx)
    assert rev.call_count == 2 and parse.call_count == 2 and r.sources


# ---------------------------------------------------------------------------------- HTML -> text


def test_html_stripping():
    html = """<div class="mw-parser-output">
<style>.x{color:red}</style>
<div class="bandeau-container metadata homonymie hatnote"><p>Ne pas confondre avec X</p></div>
<table class="infobox_v2"><tr><td>Infobox noise</td></tr></table>
<p>La <b>loi</b> est adoptée en 2014<sup class="reference"><a href="#cite_note-1">[1]</a></sup> par le Parlement[réf. nécessaire].</p>
<div class="mw-heading mw-heading2"><h2 id="Historique">Historique</h2><span class="mw-editsection">[modifier]</span></div>
<div class="mw-heading mw-heading3"><h3>Vote</h3></div>
<ul><li>Assemblée : 300 voix</li><li>Sénat : 200 voix</li></ul>
<table class="wikitable"><tr><th>Année</th><th>Taux</th></tr><tr><td>2014</td><td>7,0</td></tr></table>
<p>Un m<sup>2</sup> de surface.</p>
<div class="navbox"><p>Navigation noise</p></div>
<div class="mw-heading mw-heading2"><h2 id="Notes_et_références">Notes et références</h2></div>
<ol class="references"><li>Source 1</li></ol><p>Après les références</p>
</div>"""
    blocks = html_to_blocks(html)
    texts = [b.text for b in blocks]
    assert texts[0] == "La loi est adoptée en 2014 par le Parlement."
    assert "Assemblée : 300 voix" in texts and "Sénat : 200 voix" in texts
    assert "Année | Taux\n2014 | 7,0" in texts
    assert "Un m2 de surface." in texts  # non-reference <sup> kept
    joined = "\n".join(texts)
    for noise in ("color:red", "Ne pas confondre", "Infobox noise", "modifier", "Navigation noise", "Source 1", "Après les références", "[1]"):
        assert noise not in joined
    assert blocks[0].section == "" and blocks[1].section == "Historique > Vote"


def test_focus_excerpt_keeps_lead_relevant_blocks_in_order_and_limit():
    blocks = [Block("", "Introduction générale de l'article.")]
    blocks += [Block(f"Section {i}", f"Paragraphe {i} sans rapport. " * 20) for i in range(30)]
    blocks.insert(20, Block("Fiscalité > Montant", "Le montant de la composante carbone atteint 44,6 € par tonne en 2018."))
    blocks.insert(5, Block("Histoire", "La composante carbone est créée en 2014."))
    out = focus_excerpt(blocks, "montant composante carbone 2018", 1200)
    assert len(out) <= 1200
    assert out.startswith("Introduction générale")
    assert out.index("créée en 2014") < out.index("44,6 €")  # document order
    assert "## Fiscalité > Montant" in out
    no_focus = focus_excerpt(blocks, None, 500)
    assert no_focus.startswith("Introduction") and len(no_focus) <= 500


def test_registered_before_web():
    names = [c.name for c in build_connectors(_debate())]
    assert "wikipedia" in names and names.index("wikipedia") < names.index("web")


def test_tool_specs():
    specs = {s.name: s for s in WikipediaConnector().tools()}
    assert set(specs) == {"wikipedia_search", "wikipedia_read"}
    d = specs["wikipedia_read"].description
    assert "historiques" in d and "INSEE" in d and "recoupe" in d
    assert specs["wikipedia_read"].parameters["required"] == ["title"]


def _debate() -> Debate:
    return Debate(
        id="t", title="Débat de l'entre-deux-tours 2022", date=AS_OF,
        roster=[
            Person(id="macron", name="Emmanuel Macron", role="candidate", aliases=["Monsieur Macron", "Monsieur le Président"]),
            Person(id="lepen", name="Marine Le Pen", role="candidate", aliases=["Madame Le Pen"]),
            Person(id="m", name="Léa Salamé", role="moderator"),
        ],
    )


# ---------------------------------------------------------------------------------- live API


@pytest.mark.network
@pytest.mark.asyncio
async def test_network_read_taxe_carbone_as_of_debate(tmp_path):
    wiki = WikipediaConnector(leak_terms=leak_terms(_debate()), api=WikiApi(tmp_path))
    r = await wiki.call("wikipedia_read", {"title": "Taxe carbone en France", "focus": "montant 2018 euros par tonne"}, VerifyContext(as_of=AS_OF))
    [src] = r.sources
    assert src.url.startswith("https://fr.wikipedia.org/w/index.php?oldid=")
    assert src.date and src.date < "2022-04-20"
    assert "44,6" in r.content
    s = await wiki.call("wikipedia_search", {"query": "taxe carbone France", "limit": 5}, VerifyContext(as_of=AS_OF))
    assert "Taxe carbone en France" in s.content
