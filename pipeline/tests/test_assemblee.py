"""Tests for the Assemblée nationale connector (sic.sources.assemblee).

Offline tests build a tiny index from tests/fixtures/assemblee. The `index`/`network` tests query the
real index built by `uv run python -m sic.sources.assemblee build` and are skipped when it is missing.
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest

from sic.models import Person
from sic.sources import assemblee as an
from sic.sources.base import VerifyContext

FIX = Path(__file__).parent / "fixtures" / "assemblee"


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("an")
    # exercise the zip reader for legislature 15 (one JSON per scrutin, like the real archive)
    z15 = tmp / "scrutins_15.json.zip"
    with zipfile.ZipFile(z15, "w") as z:
        for p in sorted((FIX / "scrutins_15").glob("*.json")):
            z.write(p, f"json/{p.name}")
    path = tmp / "an.sqlite"
    an.build_index(path, [FIX / "scrutins_14", z15], FIX / "amo", fingerprint="test")
    return path


@pytest.fixture()
def conn(db: Path) -> an.AssembleeConnector:
    return an.AssembleeConnector(db)


@pytest.fixture()
def idx(db: Path) -> an.AssembleeIndex:
    return an.AssembleeIndex(db)


async def call(
    conn: an.AssembleeConnector,
    tool: str,
    args: dict,
    as_of: date | None = None,
    speaker=None,
):
    return await conn.call(tool, args, VerifyContext(as_of=as_of, speaker=speaker))


# --------------------------------------------------------------------------- unit helpers


def test_norm_and_classify() -> None:
    assert an.norm("  L’Assemblée  Nationale ") == "l'assemblee nationale"
    assert an.norm_name("Maréchal-Le Pen") == "marechal le pen"
    assert an.classify("l'ensemble du projet de loi relatif à X (première lecture).") == "ensemble"
    assert an.classify("l’ensemble de la proposition de loi visant à Y") == "ensemble"
    assert an.classify("l'amendement n° 42 de Mme X à l'article 3 du projet de loi Z") == "amendement"
    assert an.classify("le sous-amendement n° 5 à l'amendement n° 4") == "amendement"
    assert an.classify("l'article premier du projet de loi Z") == "article"
    assert an.classify("la motion de rejet préalable, déposée par M. X, du projet de loi Z") == "motion"
    assert an.classify("la motion de censure déposée en application de l'article 49, alinéa 3") == "censure"
    assert an.classify("la déclaration du Gouvernement suivie d'un vote") == "declaration"
    assert an.classify("la proposition de résolution tendant à créer une commission d'enquête") == "resolution"
    assert an.classify("la première partie du projet de loi de finances pour 2013") == "partie"


def test_parse_date() -> None:
    assert an._parse_date("2021") == "2021-01-01"
    assert an._parse_date("2021", end=True) == "2021-12-31"
    assert an._parse_date("2022-02", end=True) == "2022-02-28"
    assert an._parse_date("05/01/2022") == "2022-01-05"
    assert an._parse_date(None) is None
    with pytest.raises(ValueError):
        an._parse_date("hier")


def test_fts_query_aliases_and_stopwords() -> None:
    q = an.build_fts_query("la loi climat et résilience")
    assert q is not None
    assert '"climat"*' in q and '"resilience"*' in q and '"loi"' not in q
    # popular name expanded to the official title words
    q2 = an.build_fts_query("passe vaccinal")
    assert '"renforcant"*' in q2 and '"sanitaire"*' in q2
    assert an.build_fts_query("le la de") is None


# --------------------------------------------------------------------------- parsing / build


def test_build_parses_all_formats(idx: an.AssembleeIndex) -> None:
    con = idx.con()
    meta = idx.meta()
    assert meta["n_scrutins"] == "5"
    rows = {r["id"]: r for r in con.execute("SELECT * FROM scrutins")}
    s = rows[an.scrutin_id(15, 3738)]
    assert (s["date"], s["sort"], s["type_code"], s["kind"]) == (
        "2021-05-04",
        "adopté",
        "SPS",
        "ensemble",
    )
    assert (s["pour"], s["contre"], s["abstentions"], s["votants"]) == (
        332,
        77,
        145,
        554,
    )
    assert s["nominatif"] == 1
    assert rows[an.scrutin_id(15, 3700)]["kind"] == "amendement"
    assert rows[an.scrutin_id(15, 2706)]["kind"] == "censure"
    assert rows[an.scrutin_id(15, 2706)]["requis"] == 289
    s14 = rows[an.scrutin_id(14, 600)]
    assert s14["legislature"] == 14 and s14["nominatif"] == 0  # "dissidents only" publication
    # only GP / ASSEMBLEE / PARPOL organs are kept
    assert con.execute("SELECT count(*) FROM organes WHERE code_type = 'COMPER'").fetchone()[0] == 0
    # deputies in office (substitute PA1005 not yet sitting on 2021-05-04)
    assert s["n_exercice"] == 3


def test_positions_delegation_and_corrections(idx: an.AssembleeIndex) -> None:
    sid = an.scrutin_id(15, 3738)
    p = idx.position("PA1004", sid)
    assert p["position"] == "pour" and p["delegation"] == 1 and p["groupe"] == "PO200"
    assert idx.position("PA1001", sid)["position"] == "contre"
    nv = idx.position("PA1004", an.scrutin_id(15, 4321))
    assert nv["position"] == "non_votant" and nv["cause"] == "PSE"
    corr = idx.corrections("PA1002", an.scrutin_id(15, 3700))
    assert [(c["position"], c["dysfonctionnement"]) for c in corr] == [("pour", 0)]


def test_absent_and_out_of_office(idx: an.AssembleeIndex) -> None:
    con = idx.con()
    s2 = con.execute("SELECT * FROM scrutins WHERE id = ?", (an.scrutin_id(15, 3700),)).fetchone()
    assert idx.deputy_position("PA1004", s2)["position"] == "absent"
    dp = idx.deputy_position("PA1004", s2)
    assert dp["groupe"] == "PO200"  # group inferred from GP mandates for absentees
    # substitute: seat elected in 2017 but only sitting from datePriseFonction 2021-06-01
    assert idx.deputy_position("PA1005", s2)["position"] == "hors_mandat"
    s3 = con.execute("SELECT * FROM scrutins WHERE id = ?", (an.scrutin_id(15, 4321),)).fetchone()
    assert idx.deputy_position("PA1005", s3)["position"] == "pour"
    censure = con.execute("SELECT * FROM scrutins WHERE id = ?", (an.scrutin_id(15, 2706),)).fetchone()
    assert idx.deputy_position("PA1002", censure)["position"] == "pas_pour_censure"
    assert idx.deputy_position("PA1001", censure)["position"] == "pour"
    s14 = con.execute("SELECT * FROM scrutins WHERE id = ?", (an.scrutin_id(14, 600),)).fetchone()
    assert idx.deputy_position("PA1003", s14)["position"] == "inconnue"


def test_fts_accent_insensitive(idx: an.AssembleeIndex) -> None:
    for q in ("dérèglement climatique", "DEREGLEMENT climatique", "reglement"):
        rows, n, *_ = idx.search(q if q != "reglement" else "dereglement")
        assert n == 2, q
    rows, n, n_whole, approx, _ = idx.search("résilience climat")
    assert n == 2 and n_whole == 1 and not approx
    # whole-text vote ranked before the amendment
    assert rows[0]["kind"] == "ensemble"
    # popular-name alias: "passe vaccinal" is not in the official title
    rows, n, *_ = idx.search("passe vaccinal")
    assert n == 1 and rows[0]["numero"] == 4321
    # curated context makes the 2020 pension-reform censure motion findable
    rows, n, *_ = idx.search("système universel de retraite")
    assert any(r["numero"] == 2706 for r in rows)
    # no full match -> approximate OR fallback
    rows, n, _, approx, _ = idx.search("climat licorne")
    assert approx and n == 2


def test_find_deputies(idx: an.AssembleeIndex) -> None:
    res = idx.find_deputies("Helene DUPRE")
    assert res[0].uid == "PA1001" and res[0].score == 100
    assert all(a.uid != "PA1006" for a in res)  # never a deputy
    assert idx.find_deputies("Madame Dupré")[0].uid == "PA1001"
    assert idx.find_deputies("Dupres")[0].uid == "PA1001"  # fuzzy
    assert idx.find_deputies("PA1004")[0].name == "Éric Lefèvre"
    homonyms = idx.find_deputies("Jean Martin")
    assert {a.uid for a in homonyms} == {"PA1002", "PA1003"}
    # mandates hidden after as_of
    a = idx.find_deputies("Zoé Durand", as_of="2021-01-01")
    assert a == []
    a = idx.find_deputies("Zoé Durand", as_of="2021-07-01")[0]
    assert a.mandats[0]["date_fin"] is None  # "en cours" at as_of


# --------------------------------------------------------------------------- tools


@pytest.mark.asyncio
async def test_tool_specs(conn: an.AssembleeConnector) -> None:
    names = [t.name for t in conn.tools()]
    assert names == [
        "an_find_deputy",
        "an_search_votes",
        "an_search_scrutins",
        "an_deputy_stats",
    ]
    for t in conn.tools():
        assert t.parameters["type"] == "object" and t.parameters["required"]
        assert t.openai()["function"]["name"] == t.name


@pytest.mark.asyncio
async def test_tool_find_deputy(conn: an.AssembleeConnector) -> None:
    r = await call(conn, "an_find_deputy", {"name": "dupre"})
    assert "PA1001" in r.content and "Hélène Dupré" in r.content and "NI" in r.content
    assert r.sources[0].url == "https://www.assemblee-nationale.fr/dyn/deputes/PA1001"
    r = await call(conn, "an_find_deputy", {"name": "Lefèvre"})
    assert "LAREM (apparenté)" in r.content
    r = await call(conn, "an_find_deputy", {"name": "Personne Inconnue"})
    assert "Aucun député" in r.content and not r.sources


@pytest.mark.asyncio
async def test_tool_search_votes(conn: an.AssembleeConnector) -> None:
    r = await call(conn, "an_search_votes", {"deputy": "Hélène Dupré", "query": "climat"})
    c = r.content
    assert "2 scrutin(s)" in c and "VOTE SUR L'ENSEMBLE DU TEXTE, scrutin solennel" in c
    assert c.index("n° 3738") < c.index("n° 3700")  # whole text first
    assert "Hélène Dupré : CONTRE" in c and "son groupe (NI) : majorité contre" in c
    assert len(c) < 4000
    src = r.sources[0]
    assert src.kind == "parliament" and src.publisher == "Assemblée nationale"
    assert src.url == "https://www.assemblee-nationale.fr/dyn/15/scrutins/3738"
    assert src.title.startswith("Scrutin public n° 3738 – l'ensemble du projet de loi")
    assert src.date == "2021-05-04" and "CONTRE" in src.excerpt

    r = await call(conn, "an_search_votes", {"deputy": "Lefèvre", "query": "climat"})
    assert "par délégation" in r.content and "ABSENT (en exercice" in r.content

    r = await call(conn, "an_search_votes", {"deputy": "Jean Martin", "query": "climat"})
    assert "Plusieurs députés" in r.content and "PA1002" in r.content and "PA1003" in r.content
    assert not r.sources

    r = await call(conn, "an_search_votes", {"deputy": "PA1002", "query": "climat"})
    assert "mise au point" in r.content and "souhaitait voter pour" in r.content

    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Jean Martin", "query": "climat", "date_from": "2021"},
    )
    assert "(PA1002)" in r.content  # date range disambiguates to the 2017-2022 homonym

    r = await call(
        conn,
        "an_search_votes",
        {
            "deputy": "Jean Martin",
            "query": "climat",
            "date_from": "2013",
            "date_to": "2013",
        },
    )
    assert "PA1003" in r.content  # date range disambiguates to the 2012-2017 homonym

    r = await call(conn, "an_search_votes", {"deputy": "Zoé Durand", "query": "climat"})
    assert "0 scrutin(s)" in r.content and "2 autre(s) scrutin(s) correspondant(s) hors de ses mandats" in r.content

    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Hélène Dupré", "query": "climat", "whole_text_only": True},
    )
    assert "n° 3700" not in r.content and len(r.sources) == 1

    r = await call(conn, "an_search_votes", {"deputy": "Dupré", "query": "retraite"})
    assert "MOTION DE CENSURE" in r.content and "système universel de retraite" in r.content


@pytest.mark.asyncio
async def test_as_of_filtering(conn: an.AssembleeConnector) -> None:
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Dupré", "query": "crise sanitaire"},
        as_of=date(2021, 12, 31),
    )
    assert "n° 4321" not in r.content and "0 scrutin(s)" in r.content
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Dupré", "query": "crise sanitaire"},
        as_of=date(2022, 4, 20),
    )
    assert "n° 4321" in r.content and "Hélène Dupré : CONTRE" in r.content
    r = await call(conn, "an_search_scrutins", {"query": "climat"}, as_of=date(2021, 5, 1))
    assert "n° 3738" not in r.content and "n° 3700" in r.content
    r = await call(conn, "an_deputy_stats", {"deputy": "Dupré"}, as_of=date(2021, 5, 1))
    assert "2020-03-03 → 2021-04-15" not in r.content  # censure excluded from stats
    assert "1 scrutins pendant son mandat" in r.content


@pytest.mark.asyncio
async def test_tool_search_scrutins(conn: an.AssembleeConnector) -> None:
    r = await call(
        conn,
        "an_search_scrutins",
        {"query": "dérèglement climatique résilience", "legislature": 15},
    )
    c = r.content
    assert "LAREM: pour (2-0-0)" in c and "NI: contre (0-1-0)" in c
    assert "adopté (pour 332, contre 77, abst. 145)" in c
    assert r.sources[0].url.endswith("/15/scrutins/3738")
    r = await call(conn, "an_search_scrutins", {"query": "mariage couples même sexe"})
    assert "n° 600 (L14)" in r.content and "SRC: pour" in r.content
    r = await call(
        conn,
        "an_search_scrutins",
        {"query": "censure", "date_from": "2020-03", "date_to": "2020-03"},
    )
    assert "pour la censure" in r.content and "majorité requise 289" in r.content


@pytest.mark.asyncio
async def test_tool_deputy_stats(conn: an.AssembleeConnector) -> None:
    r = await call(conn, "an_deputy_stats", {"deputy": "Éric Lefèvre"})
    c = r.content
    # 3 nominative non-censure scrutins in L15 while in office: pour (deleg), absent, non-votant
    assert "3 scrutins pendant son mandat" in c and "a voté à 1 (33,3 %)" in c
    assert "non-votant 1" in c and "(absent) 1" in c and "délégation 1" in c
    assert "Prudence" in c
    assert r.sources and r.sources[0].url.endswith("/deputes/PA1004")


@pytest.mark.asyncio
async def test_speaker_context_and_errors(conn: an.AssembleeConnector, tmp_path: Path) -> None:
    sp = Person(
        id="dupre",
        name="Hélène Dupré",
        role="candidate",
        aliases=["Madame Dupré"],
        an_id="PA1001",
    )
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Madame Dupré", "query": "climat"},
        speaker=sp,
    )
    assert "(PA1001)" in r.content
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Dupré", "query": "x", "date_from": "n'importe"},
    )
    assert r.content.startswith("Erreur d'argument")
    r = await call(conn, "an_unknown", {})
    assert "inconnu" in r.content
    missing = an.AssembleeConnector(tmp_path / "nope.sqlite")
    r = await call(missing, "an_find_deputy", {"name": "x"})
    assert "indisponible" in r.content


def test_build_is_atomic_and_rebuildable(db: Path, tmp_path: Path) -> None:
    dst = tmp_path / "copy.sqlite"
    shutil.copy(db, dst)
    an.build_index(dst, [FIX / "scrutins_15"], FIX / "amo", fingerprint="again")
    assert an.AssembleeIndex(dst).meta()["n_scrutins"] == "4"
    assert not dst.with_suffix(".sqlite.tmp").exists()


# --------------------------------------------------------------------------- real data

needs_index = pytest.mark.skipif(not an.DB_PATH.exists(), reason="real AN index not built")


@pytest.mark.index
@needs_index
@pytest.mark.asyncio
async def test_real_lepen_passe_vaccinal() -> None:
    conn = an.AssembleeConnector()
    r = await call(conn, "an_find_deputy", {"name": "Le Pen"}, as_of=date(2022, 4, 20))
    assert "PA720614" in r.content
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "Marine Le Pen", "query": "passe vaccinal", "whole_text_only": True},
        as_of=date(2022, 4, 20),
    )
    # loi du 22 janvier 2022 renforçant les outils de gestion de la crise sanitaire (passe vaccinal)
    assert "n° 4279" in r.content and "Marine Le Pen : CONTRE" in r.content
    assert len(r.content) < 4000


@pytest.mark.index
@needs_index
@pytest.mark.asyncio
async def test_real_retraite_and_climat() -> None:
    conn = an.AssembleeConnector()
    r = await call(
        conn,
        "an_search_votes",
        {"deputy": "PA720614", "query": "retraite", "whole_text_only": True},
    )
    assert "n° 1241 (L16)" in r.content  # RN censure motion on the 2023 pension reform
    r = await call(
        conn,
        "an_search_scrutins",
        {"query": "climat et résilience", "whole_text_only": True},
    )
    assert "n° 3738 (L15)" in r.content and "LAREM: pour" in r.content


@pytest.mark.network
def test_network_open_data_urls_reachable() -> None:
    import httpx

    for url in [*an.SCRUTIN_URLS.values(), an.AMO_URL]:
        r = httpx.head(url, timeout=30, follow_redirects=True)
        assert r.status_code == 200, url
