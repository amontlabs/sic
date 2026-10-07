"""Web search connector: parsing, ranking, as_of filtering and leak guard (no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest
import respx

from sic.models import Debate, Person
from sic.sources import leak_terms, surname
from sic.sources import web as webmod
from sic.sources.base import VerifyContext
from sic.sources.web import (
    LeakGuard,
    Spacer,
    UnsafeUrl,
    WebConnector,
    assert_public_url,
    classify,
    parse_brave,
    parse_date,
    publication_date,
    source_kind,
    url_date,
    url_date_range,
)

AS_OF = date(2022, 4, 20)

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


# ---------------------------------------------------------------------------------- dates


def test_url_dates_french_media():
    assert url_date("https://www.lemonde.fr/politique/article/2022/04/21/debat-ce-qu-il-faut-retenir_6123_823448.html") == date(2022, 4, 21)
    assert url_date("https://www.liberation.fr/politique/elections/debat-qui-a-gagne-20220421_ABCDEF6Q/") == date(2022, 4, 21)
    assert url_date("https://www.lefigaro.fr/politique/la-revanche-de-marine-le-pen-20260703") == date(2026, 7, 3)
    assert url_date("https://www.lefigaro.fr/conjoncture/2018/02/14/20002-20180214ARTFIG00337-l-industrie.php") == date(2018, 2, 14)
    assert url_date("https://www.20minutes.fr/politique/3260323-20220327-presidentielle-2022-retraite") == date(2022, 3, 27)
    assert url_date("https://www.france24.com/fr/info-en-continu/20260922-levee-des-sanctions") == date(2026, 9, 22)
    assert url_date("https://www.leparisien.fr/international/guerre-18-04-2022-S43HFK4ELRBQ3F.php") == date(2022, 4, 18)
    assert url_date("https://www.caixinglobal.com/2021-05-11/census-101709470.html") == date(2021, 5, 11)
    assert url_date_range("https://x.gouv.fr/sites/files/2026-09/Etude.pdf") == (date(2026, 9, 1), date(2026, 9, 30))
    assert url_date_range("https://www.senat.fr/questions/base/2020/qSEQ200415702.html") == (date(2020, 1, 1), date(2020, 12, 31))
    assert url_date_range("https://www.insee.fr/fr/statistiques/6051733") is None
    assert url_date_range("https://www.institutmontaigne.org/presidentielle-2022/marine-le-pen/x/") is None


def test_publication_date_prefers_explicit_then_url_over_text_dates():
    # Regression: the first ISO date of the snippet used to win over the real publication date.
    pd = publication_date("2023-02-01", "https://blog.example.com/p", "Le décret du 2021-03-04 prévoit…")
    assert pd and pd.lo == date(2023, 2, 1) and pd.origin == "meta"
    pd = publication_date("", "https://www.lemonde.fr/x/article/2023/01/05/a_1_2.html", "2021-03-04 : chiffres")
    assert pd and pd.lo == date(2023, 1, 5) and pd.origin == "url"
    pd = publication_date("", "https://blog.example.com/p", "Mar 2, 2023 — texte mentionnant 2021-03-04")
    assert pd and pd.lo == date(2023, 3, 2) and pd.origin == "snippet"
    pd = publication_date("", "https://blog.example.com/p", "texte mentionnant 2021-03-04")
    assert pd and pd.origin == "text"  # only proves the page is not older than that
    assert parse_date("2022-04-21T08:00:00+02:00") == date(2022, 4, 21)
    assert parse_date("Thu, 21 Apr 2022 08:00:00 GMT") == date(2022, 4, 21)
    assert parse_date("Publié le 12 avril 2022 à 10h") == date(2022, 4, 12)
    assert parse_date("21 avr. 2022 — texte") == date(2022, 4, 21)


def _engine(w: WebConnector, monkeypatch, results, zai=None):
    async def fake_brave(q):
        return results

    async def fake_zai(q, d):
        return zai or []

    monkeypatch.setattr(w, "_brave_search", fake_brave)
    monkeypatch.setattr(w, "_raw_search", fake_zai)


@pytest.mark.asyncio
async def test_strict_mode_dates_and_undated_results(monkeypatch):
    w = WebConnector()
    _engine(w, monkeypatch, [
        # 2023 article whose snippet cites an older date: must be dropped (URL date wins)
        {"title": "Retraites", "link": "https://www.lemonde.fr/les-decodeurs/article/2023/01/10/retraites_1_2.html",
         "content": "Le décret 2021-03-04 …"},
        {"title": "Fact-check non daté", "link": "https://www.tf1info.fr/politique/verif-123.html", "content": "chiffres"},
        {"title": "Blog non daté", "link": "https://blog.example.com/p", "content": "chiffres"},
        {"title": "Mentionne 2025", "link": "https://blog.example.com/q", "content": "Jan 3, 2022 — données mises à jour 2025-01-02"},
        {"title": "INSEE non daté", "link": "https://www.insee.fr/fr/statistiques/1", "content": "chiffres"},
        {"title": "Libé avant", "link": "https://www.liberation.fr/checknews/x-20220401_ABC/", "content": "vérif"},
    ])
    r = await w.call("web_search", {"query": "retraites"}, VerifyContext(as_of=AS_OF))
    urls = [s.url for s in r.sources]
    assert urls == ["https://www.insee.fr/fr/statistiques/1", "https://www.liberation.fr/checknews/x-20220401_ABC/"]
    assert "4 résultat(s) écarté(s) : 2 postérieur(s), 2 non daté(s) hors sources officielles" in r.content
    assert r.sources[0].date is None and r.sources[1].date == "2022-04-01" and "non daté" in r.content
    r2 = await w.call("web_search", {"query": "retraites"}, VerifyContext())
    assert len(r2.sources) == 6
    assert next(s for s in r2.sources if "blog.example.com/p" in (s.url or "")).date is None


@pytest.mark.asyncio
async def test_vague_links_flagged_in_excerpt(monkeypatch):
    w = WebConnector()
    _engine(w, monkeypatch, [
        {"title": "Accueil", "link": "https://www.insee.fr/", "content": "homepage"},
        {"title": "Article", "link": "https://www.insee.fr/fr/statistiques/1", "content": "Feb 1, 2022 — chiffre"},
    ])
    r = await w.call("web_search", {"query": "x"}, VerifyContext())
    assert [s.url for s in r.sources] == ["https://www.insee.fr/fr/statistiques/1", "https://www.insee.fr/"]
    assert r.sources[1].excerpt == "(lien imprécis) homepage" and r.sources[0].excerpt == "Feb 1, 2022 — chiffre"


# ---------------------------------------------------------------------------------- leak guard


def _debate() -> Debate:
    return Debate(
        id="t", title="Débat de l'entre-deux-tours 2022", date=AS_OF,
        roster=[
            Person(id="macron", name="Emmanuel Macron", role="candidate", aliases=["Monsieur Macron", "Monsieur le Président"]),
            Person(id="lepen", name="Marine Le Pen", role="candidate", aliases=["Madame Le Pen", "Marine Le Pen"]),
            Person(id="m", name="Léa Salamé", role="moderator", aliases=["Madame Salamé"]),
        ],
    )


def test_leak_terms_and_matching():
    deb = _debate()
    assert surname(deb.roster[1]) == "Le Pen" and surname(deb.roster[0]) == "Macron"
    terms = leak_terms(deb)
    assert "20 avril 2022" in terms and "entre-deux-tours" in terms and not any("Salamé" in t for t in terms)
    g = LeakGuard(terms)
    for text in (
        "DÉBAT Macron-Le Pen : ce qu'il faut retenir",
        "Le débat télévisé entre Emmanuel Macron et Marine Le Pen",
        "débat d’entre-deux-tours",
        "Ce qu'a dit Marine Le Pen hier soir",
        "Lors du débat, la candidate a affirmé",
        "Le face à face a tourné court",
        "mercredi 20 avril 2022 sur TF1",
        "/politique/debat-presidentiel-macron-le-pen-20220421",
    ):
        assert g.find(text), text
    for text in (
        "Le grand débat national voulu par Emmanuel Macron en 2019",
        "lors du débat parlementaire sur la loi de finances",
        "Le taux de chômage au quatrième trimestre 2021",
    ):
        assert g.find(text) is None, text
    # weak terms ("hier soir", "lors du débat"…) are not applied to official pages
    assert g.find("Le ministre a déclaré hier soir", official=True) is None
    assert g.find("Le ministre a déclaré hier soir")
    assert leak_terms(None) == []
    plain = deb.model_copy(update={"title": "Débat du premier tour"})
    assert "entre-deux-tours" not in leak_terms(plain)


# ---------------------------------------------------------------------------------- web_read


def _reader(w: WebConnector, monkeypatch, tmp_path, payload: dict):
    w.cache_dir = tmp_path

    async def fake_call(tool, args):
        return json.dumps(payload)

    monkeypatch.setattr(w.reader_client, "call", fake_call)
    monkeypatch.setattr(webmod, "_resolve", lambda host, port: ["93.184.215.14"])


@pytest.mark.asyncio
async def test_read_strict_mode_dates_and_warning(monkeypatch, tmp_path):
    w = WebConnector(leak_terms=leak_terms(_debate()))
    _reader(w, monkeypatch, tmp_path, {"title": "Article", "content": "Le chômage est à 7,4 %.", "published_time": ""})
    ctx = VerifyContext(as_of=AS_OF)
    r = await w.call("web_read", {"url": "https://www.lemonde.fr/economie/article/2022/04/22/chomage_1_2.html"}, ctx)
    assert "après le 2022-04-20" in r.content and not r.sources  # URL date
    r = await w.call("web_read", {"url": "https://blog.example.com/chomage"}, ctx)
    assert r.content.startswith("ATTENTION : date de publication inconnue") and "2022-04-20" in r.content.split("\n")[0]
    r = await w.call("web_read", {"url": "https://www.insee.fr/fr/statistiques/1"}, ctx)
    assert not r.content.startswith("ATTENTION") and "7,4 %" in r.content  # official: no warning
    r = await w.call("web_read", {"url": "https://blog.example.com/chomage"}, VerifyContext())
    assert not r.content.startswith("ATTENTION")


@pytest.mark.asyncio
async def test_read_uses_reader_published_time_and_leak_guard(monkeypatch, tmp_path):
    w = WebConnector(leak_terms=leak_terms(_debate()))
    _reader(w, monkeypatch, tmp_path, {"title": "A", "content": "texte", "published_time": "2022-04-21T07:00:00+02:00"})
    r = await w.call("web_read", {"url": "https://blog.example.com/a"}, VerifyContext(as_of=AS_OF))
    assert "Page publiée le 2022-04-21" in r.content
    _reader(w, monkeypatch, tmp_path, {"title": "B", "content": "Lors du débat d'entre-deux-tours, Marine Le Pen…", "published_time": "2022-04-20"})
    r = await w.call("web_read", {"url": "https://blog.example.com/b"}, VerifyContext(as_of=AS_OF))
    assert "commente le débat" in r.content
    _reader(w, monkeypatch, tmp_path, {"title": "C", "content": "texte", "published_time": "2022-03-01"})
    r = await w.call("web_read", {"url": "https://blog.example.com/c"}, VerifyContext(as_of=AS_OF))
    assert r.sources[0].date == "2022-03-01" and not r.content.startswith("ATTENTION")


# ---------------------------------------------------------------------------------- SSRF


@pytest.mark.asyncio
async def test_ssrf_guard(monkeypatch, tmp_path):
    w = WebConnector()
    w.cache_dir = tmp_path
    resolved = {"internal.example": ["10.0.0.5"], "public.example": ["93.184.215.14"], "v6.example": ["::ffff:127.0.0.1"]}
    monkeypatch.setattr(webmod, "_resolve", lambda host, port: resolved[host])

    async def must_not_fetch(*a, **k):
        raise AssertionError("fetched")

    monkeypatch.setattr(w.reader_client, "call", must_not_fetch)
    for url in ("file:///etc/passwd", "ftp://public.example/x", "gopher://x", "http://127.0.0.1:8787/api", "http://169.254.169.254/latest/meta-data",
                "http://[::1]/", "http://localhost/x", "http://internal.example/x", "http://v6.example/", "http://user:pw@public.example/"):
        r = await w.call("web_read", {"url": url}, VerifyContext())
        assert "refusée" in r.content or "invalide" in r.content, url
        assert r.sources == []
    await assert_public_url("https://public.example/page")
    with pytest.raises(UnsafeUrl):
        await assert_public_url("http://0.0.0.0/")


@pytest.mark.asyncio
@respx.mock
async def test_direct_fetch_checks_every_redirect_hop(monkeypatch):
    resolved = {"public.example": ["93.184.215.14"], "other.example": ["93.184.215.15"], "internal.example": ["192.168.1.10"]}
    monkeypatch.setattr(webmod, "_resolve", lambda host, port: resolved[host])
    respx.get("https://public.example/a").mock(return_value=httpx.Response(302, headers={"location": "https://other.example/b"}))
    respx.get("https://other.example/b").mock(return_value=httpx.Response(200, html="<title>B</title><p>Contenu</p>"))
    out = json.loads(await webmod._direct_fetch("https://public.example/a"))
    assert out["title"] == "B" and "Contenu" in out["content"]
    respx.get("https://public.example/evil").mock(return_value=httpx.Response(301, headers={"location": "http://internal.example/admin"}))
    internal = respx.get("http://internal.example/admin").mock(return_value=httpx.Response(200, text="secret"))
    with pytest.raises(UnsafeUrl):
        await webmod._direct_fetch("https://public.example/evil")
    assert internal.call_count == 0
    respx.get("https://public.example/loop").mock(return_value=httpx.Response(302, headers={"location": "/loop"}))
    with pytest.raises(RuntimeError, match="redirections"):
        await webmod._direct_fetch("https://public.example/loop")


# ---------------------------------------------------------------------------------- load / timeouts


def test_spacer_reserves_slots_without_holding_a_lock():
    s = Spacer()
    assert s.reserve(2.0) == 0
    assert s.reserve(2.0) == pytest.approx(2.0, abs=0.05)
    assert s.reserve(2.0, max_wait=3.0) is None  # would wait ~4 s: skip instead of queueing
    s.cooldown(60)
    assert s.reserve(0.0) is None
    s.reset()
    assert s.reserve(0.0) == 0


@pytest.mark.asyncio
async def test_slow_provider_does_not_lose_other_results(monkeypatch):
    w = WebConnector()
    monkeypatch.setattr(webmod, "PROVIDER_TIMEOUT", 0.05)
    brave_queries: list[str] = []

    async def slow_brave(q):
        brave_queries.append(q)
        await asyncio.sleep(5)
        return []

    async def fast_zai(q, d):
        return [{"title": "Rapport", "link": "https://www.ccomptes.fr/fr/publications/rapport-2021", "content": "Jan 5, 2022 — dette"}]

    monkeypatch.setattr(w, "_brave_search", slow_brave)
    monkeypatch.setattr(w, "_raw_search", fast_zai)
    r = await asyncio.wait_for(w.call("web_search", {"query": "dette", "domains": ["insee.fr", "ccomptes.fr", "senat.fr"]}, VerifyContext()), 2)
    assert [s.url for s in r.sources] == ["https://www.ccomptes.fr/fr/publications/rapport-2021"]
    assert "2 moteur(s) sans réponse à temps" in r.content
    assert len(brave_queries) == 2  # Brave capped at 2 queries per search


@pytest.mark.asyncio
@respx.mock
async def test_brave_requests_are_spaced_not_serialized(monkeypatch, tmp_path):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    webmod._BRAVE.reset()
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)
        await real_sleep(0)

    monkeypatch.setattr(webmod.asyncio, "sleep", fake_sleep)
    respx.get("https://search.brave.com/search").mock(return_value=httpx.Response(200, text=BRAVE_HTML))
    w = WebConnector()
    w.cache_dir = tmp_path
    res = await asyncio.gather(*(w._brave_search(q) for q in "abcdef"))
    assert [len(r) for r in res[:5]] == [1, 1, 1, 1, 1]
    assert res[5] == []  # 6th would wait 10 s > BRAVE_MAX_WAIT: skipped rather than queued
    assert sorted(round(s) for s in sleeps) == [2, 4, 6, 8]  # spaced by 2 s, requests not serialized behind a lock
    webmod._BRAVE.reset()


# ---------------------------------------------------------------------------------- cache


@pytest.mark.asyncio
async def test_cache_is_atomic_and_corrupt_entries_are_misses(monkeypatch, tmp_path):
    w = WebConnector()
    w.cache_dir = tmp_path
    calls = 0

    async def fake_call(tool, args):
        nonlocal calls
        calls += 1
        return json.dumps([{"title": "T", "link": "https://www.insee.fr/fr/statistiques/1", "content": "c"}])

    monkeypatch.setattr(w.search_client, "call", fake_call)
    assert len(await w._raw_search("q", None)) == 1
    assert len(await w._raw_search("q", None)) == 1 and calls == 1
    files = list((tmp_path / "search").iterdir())
    assert len(files) == 1 and not files[0].name.startswith(".")
    files[0].write_text('[{"title": "T", "li')  # truncated write
    assert len(await w._raw_search("q", None)) == 1 and calls == 2
    assert json.loads(files[0].read_text())[0]["title"] == "T"
    assert [f.name for f in (tmp_path / "search").iterdir()] == [files[0].name]  # no temp leftovers
