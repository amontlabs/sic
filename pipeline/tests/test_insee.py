"""INSEE BDM connector: SDMX parsing, publication-date filtering, derived values, search ranking."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from sic.sources.base import VerifyContext
from sic.sources.insee import (
    API,
    CATALOG_PATH,
    KEY_SERIES_PATH,
    CachedHttp,
    Catalog,
    CatalogEntry,
    InseeConnector,
    load_catalog,
    load_key_series,
    parse_error,
    parse_sdmx,
    period_bounds,
    publication_lag,
    series_kind,
    tokenize,
)

FIX = Path(__file__).parent / "fixtures"
UNEMP = "001688527"  # taux de chômage BIT, France hors Mayotte, CVS
CPI = "011814630"  # IPC base 2025, ensemble
POP = "001641586"  # population au 1er janvier


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def mini_catalog(path: Path) -> Path:
    rows = [
        {"id": UNEMP, "t": "Taux de chômage au sens du BIT - Ensemble - France hors Mayotte - Données CVS", "f": "T",
         "df": "CHOMAGE-TRIM-NATIONAL", "first": "1975-Q1", "last": "2026-Q2", "area": "FR-D976", "corr": "CVS"},
        {"id": "000000001", "t": "Taux de chômage localisé - Ensemble - Bretagne - Données CVS", "f": "T",
         "df": "TAUX-CHOMAGE", "first": "1982-Q1", "last": "2026-Q2", "area": "R53", "corr": "CVS"},
        {"id": "000000002", "t": "Taux de chômage au sens du BIT - Ensemble - France hors Mayotte - Données brutes - Série arrêtée",
         "f": "T", "df": "CHOMAGE-TRIM-NATIONAL", "first": "1975-Q1", "last": "2014-Q4", "area": "FR-D976", "stop": True},
        {"id": CPI, "t": "Indice des prix à la consommation - Base 2025 - Ensemble des ménages - France - Nomenclature Coicop : 00 - Ensemble",
         "f": "M", "df": "IPC-2025", "first": "1996-01", "last": "2026-09", "area": "FE"},
        {"id": POP, "t": "Population totale au 1er janvier - France (inclus Mayotte à partir de 2014)", "f": "A",
         "df": "POPULATION-STRUCTURE", "first": "1982", "last": "2026", "area": "FE"},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def conn(tmp_path: Path) -> InseeConnector:
    http = CachedHttp(tmp_path / "cache", min_interval=0, retries=1, backoff=0)
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"series": []}), encoding="utf-8")
    return InseeConnector(http=http, catalog_path=mini_catalog(tmp_path / "cat.jsonl.gz"), key_series_path=keys)


def mock_series(idbank: str, body: str | None = None, status: int = 200) -> respx.Route:
    return respx.get(f"{API}/data/SERIES_BDM/{idbank}").mock(
        return_value=httpx.Response(status, text=body if body is not None else fixture(f"insee_{idbank}.xml"))
    )


# ----------------------------------------------------------------------------- parsing


def test_period_bounds():
    assert period_bounds("2021") == (date(2021, 1, 1), date(2021, 12, 31))
    assert period_bounds("2021-Q4") == (date(2021, 10, 1), date(2021, 12, 31))
    assert period_bounds("2022-02") == (date(2022, 2, 1), date(2022, 2, 28))
    assert period_bounds("2022-S2") == (date(2022, 7, 1), date(2022, 12, 31))
    with pytest.raises(ValueError):
        period_bounds("hier")


def test_parse_sdmx_sorts_ascending_and_reads_attributes():
    (s,) = parse_sdmx(fixture(f"insee_{UNEMP}.xml"))
    assert s.idbank == UNEMP and s.freq == "T"
    assert s.title.startswith("Taux de chômage au sens du BIT")
    assert s.obs[0] == ("2019-Q1", 8.8)
    assert dict(s.obs)["2021-Q4"] == 7.4
    assert [p for p, _ in s.obs] == sorted(p for p, _ in s.obs)
    assert s.unit == "%" and series_kind(s) == "rate"
    (cpi,) = parse_sdmx(fixture(f"insee_{CPI}.xml"))
    assert series_kind(cpi) == "index"


def test_parse_sdmx_handles_missing_values():
    xml = (
        '<m:StructureSpecificData xmlns:m="x"><m:DataSet><Series IDBANK="1" FREQ="A" TITLE_FR="t">'
        '<Obs TIME_PERIOD="2020" OBS_VALUE="NaN"/><Obs TIME_PERIOD="2019" OBS_VALUE="1.5"/>'
        "</Series></m:DataSet></m:StructureSpecificData>"
    )
    (s,) = parse_sdmx(xml)
    assert s.obs == [("2019", 1.5), ("2020", None)]


def test_parse_error_split_hints():
    body = (
        '<mes:Error xmlns:mes="m"><mes:ErrorMessage code="510"><com:Text xmlns:com="c">trop volumineuse</com:Text>'
        '<com:Text xmlns:com="c">/data/IPC-2025/M...........FALSE</com:Text></mes:ErrorMessage></mes:Error>'
    )
    code, texts = parse_error(body)
    assert code == "510" and "/data/IPC-2025/M...........FALSE" in texts


def test_publication_lags():
    assert publication_lag("T", "CHOMAGE-TRIM-NATIONAL") == 45
    assert publication_lag("T", "DETTE-TRIM-APU-2020") == 85
    assert publication_lag("M", "IPC-2025") == 15
    assert publication_lag("M", "IPC-PM-2015") == 15
    assert publication_lag("A", "CNA-2014-DETTE-APU") == 90
    assert publication_lag("A", "NAISSANCES-FECONDITE") == 20
    assert publication_lag("M", "") == 28 and publication_lag("A", "") == 90


# ----------------------------------------------------------------------------- get_series


@pytest.mark.asyncio
@respx.mock
async def test_get_series_as_of_hides_unpublished_quarter(conn):
    mock_series(UNEMP)
    res = await conn.call("insee_get_series", {"idbank": UNEMP}, VerifyContext(as_of=date(2022, 4, 20)))
    c = res.content
    # 2022-Q1 had ended but was only published mid-May 2022: excluded, and said so
    assert "2022-Q1:" not in c and "2022-Q2" not in c.split("Observations")[1]
    assert "pas encore publiées à la date du débat: 2022-Q1" in c
    assert "2021-Q4: 7.4 | -0.5 pt | -0.7 pt" in c
    assert "Dernière valeur retenue: 2021-Q4 = 7.4 %" in c
    assert "dernières données révisées" in c
    (src,) = res.sources
    assert src.kind == "official_data" and src.publisher == "INSEE"
    assert src.url == f"https://www.insee.fr/fr/statistiques/serie/{UNEMP}"
    assert src.date == "2021-Q4" and "7.4" in src.excerpt
    assert len(c) < 4000


@pytest.mark.asyncio
@respx.mock
async def test_get_series_publication_lag_boundary(conn):
    mock_series(UNEMP)
    # 2022-Q1 ends 2022-03-31; +45 days = 2022-05-15
    before = await conn.call("insee_get_series", {"idbank": UNEMP}, VerifyContext(as_of=date(2022, 5, 14)))
    after = await conn.call("insee_get_series", {"idbank": UNEMP}, VerifyContext(as_of=date(2022, 5, 15)))
    assert "2022-Q1:" not in before.content
    assert "2022-Q1: 7.4" in after.content and after.sources[0].date == "2022-Q1"


@pytest.mark.asyncio
@respx.mock
async def test_get_series_without_as_of_and_window(conn):
    mock_series(UNEMP)
    res = await conn.call("insee_get_series", {"idbank": UNEMP, "start": "2021", "end": "2021-Q4"}, VerifyContext())
    lines = [line for line in res.content.splitlines() if line.startswith("2021-Q")]
    assert [line.split(":")[0] for line in lines] == ["2021-Q1", "2021-Q2", "2021-Q3", "2021-Q4"]
    assert "Moyennes annuelles:" in res.content and "2021: 7.8" in res.content
    assert "Écart 2021-Q1 → 2021-Q4: -0.8 points" in res.content
    res2 = await conn.call("insee_get_series", {"idbank": UNEMP, "last_n": 2}, VerifyContext())
    assert "2022-Q4:" in res2.content and "2022-Q2:" not in res2.content


@pytest.mark.asyncio
@respx.mock
async def test_cpi_derived_year_over_year_and_annual_average(conn):
    mock_series(CPI)
    res = await conn.call("insee_get_series", {"idbank": CPI, "last_n": 3}, VerifyContext(as_of=date(2022, 4, 20)))
    (s,) = parse_sdmx(fixture(f"insee_{CPI}.xml"))
    v = dict(s.obs)
    yoy = (v["2022-03"] / v["2021-03"] - 1) * 100
    mom = (v["2022-03"] / v["2022-02"] - 1) * 100
    assert f"2022-03: {v['2022-03']:.2f} | {mom:+.1f} % | {yoy:+.1f} %" in res.content
    # March CPI (final) is out mid-April: kept; April is not finished: excluded
    assert "2022-04:" not in res.content
    avg = {y: sum(v[f"{y}-{m:02d}"] for m in range(1, 13)) / 12 for y in (2020, 2021)}
    assert f"({(avg[2021] / avg[2020] - 1) * 100:+.1f} % en moyenne annuelle)" in res.content


@pytest.mark.asyncio
@respx.mock
async def test_stock_on_january_first_is_known_at_period_start(conn):
    mock_series(POP)
    res = await conn.call("insee_get_series", {"idbank": POP}, VerifyContext(as_of=date(2022, 4, 20)))
    assert "2022:" in res.content and "2023:" not in res.content
    assert res.sources[0].date == "2022"


@pytest.mark.asyncio
@respx.mock
async def test_unknown_series_and_network_errors_do_not_raise(conn):
    mock_series("000000009", body="<mes:Error/>", status=404)
    res = await conn.call("insee_get_series", {"idbank": "9"}, VerifyContext())
    assert "introuvable" in res.content and not res.sources
    respx.get(f"{API}/data/SERIES_BDM/000000008").mock(side_effect=httpx.ConnectError("boom"))
    res = await conn.call("insee_get_series", {"idbank": "000000008"}, VerifyContext())
    assert res.content.startswith("Erreur INSEE") and not res.sources
    res = await conn.call("insee_get_series", {"idbank": ""}, VerifyContext())
    assert "idbank manquant" in res.content


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_429_then_disk_cache(conn):
    route = respx.get(f"{API}/data/SERIES_BDM/{UNEMP}").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, text=fixture(f"insee_{UNEMP}.xml"))]
    )
    a = await conn.call("insee_get_series", {"idbank": UNEMP}, VerifyContext())
    b = await conn.call("insee_get_series", {"idbank": UNEMP}, VerifyContext())
    assert route.call_count == 2  # one 429 + one 200; the second call is served from disk
    assert a.content == b.content and "2022-Q4" in a.content


# ----------------------------------------------------------------------------- search


def test_tokenize_folds_accents_and_plurals():
    assert tokenize("Défaillances d'entreprises") == tokenize("defaillance entreprise")
    assert tokenize("Taux de CHÔMAGE") == tokenize("taux chomage")
    assert "de" not in tokenize("taux de chômage")


def test_search_prior_prefers_national_current_series(tmp_path):
    cat = Catalog(load_catalog(mini_catalog(tmp_path / "c.jsonl.gz")))
    hits = cat.search("taux de chômage", limit=3)
    assert hits[0][0].id == UNEMP
    assert [e.id for e, _ in hits].index("000000002") > 0  # stopped raw series ranked lower
    assert cat.search(UNEMP)[0][0].id == UNEMP  # direct idbank lookup


@pytest.mark.asyncio
async def test_search_tool_output(conn):
    res = await conn.call("insee_search_series", {"query": "taux de chômage", "limit": 2}, VerifyContext())
    assert UNEMP in res.content.splitlines()[1] and "1975-Q1→2026-Q2" in res.content
    empty = await conn.call("insee_search_series", {"query": "zzzz qqqq"}, VerifyContext())
    assert "Aucune série" in empty.content


real_catalog = pytest.mark.skipif(not CATALOG_PATH.exists(), reason="committed catalog missing")


@real_catalog
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("taux de chômage", "001688527"),  # BIT, France hors Mayotte, CVS
        ("inflation", "011814632"),  # IPC glissement annuel
        ("dette publique", "010777608"),  # dette Maastricht en % du PIB
        ("SMIC", "000822484"),
        ("défaillances d'entreprises", "001656101"),
        ("naissances", "001641590"),
        ("balance commerciale", "001568614"),
        ("chômage des jeunes", "001688537"),
    ],
)
def test_search_ranking_on_committed_catalog(query, expected):
    cat = _real_catalog()
    hits = cat.search(query, limit=5, as_of=date(2022, 4, 20))
    assert hits[0][0].id == expected, [(e.id, e.t[:60]) for e, _ in hits]


@real_catalog
def test_search_debt_returns_maastricht_series():
    hits = _real_catalog().search("dette publique", limit=3)
    assert all("maastricht" in e.t.lower() for e, _ in hits[:2])


_CAT: Catalog | None = None


def _real_catalog() -> Catalog:
    global _CAT
    if _CAT is None:
        _CAT = Catalog(load_catalog(), load_key_series())
    return _CAT


def test_key_series_file_is_consistent():
    keys = load_key_series(KEY_SERIES_PATH)
    assert 25 <= len(keys) <= 45
    ids = [k["idbank"] for k in keys]
    assert len(ids) == len(set(ids))
    for k in keys:
        assert len(k["idbank"]) == 9 and k["idbank"].isdigit()
        assert k["title"] and k["description"] and k["freq"] in {"A", "T", "M"}


def test_tool_specs_list_key_series():
    conn = InseeConnector()
    specs = {t.name: t for t in conn.tools()}
    assert set(specs) == {"insee_search_series", "insee_get_series"}
    assert "001688527" in specs["insee_search_series"].description
    assert specs["insee_get_series"].parameters["required"] == ["idbank"]
    for t in specs.values():
        assert t.openai()["function"]["parameters"]["type"] == "object"


def test_catalog_entry_roundtrip():
    e = CatalogEntry.from_dict({"id": "1", "t": "x", "unknown": 3})
    assert e.id == "1" and e.t == "x"


# ----------------------------------------------------------------------------- network


@pytest.mark.network
@pytest.mark.asyncio
async def test_real_api_unemployment(tmp_path):
    conn = InseeConnector(http=CachedHttp(tmp_path, retries=2))
    res = await conn.call("insee_get_series", {"idbank": UNEMP, "start": "2021", "end": "2021"}, VerifyContext())
    assert "2021-Q4: 7.4" in res.content, res.content
    assert res.sources and res.sources[0].title.startswith("Taux de chômage au sens du BIT")
