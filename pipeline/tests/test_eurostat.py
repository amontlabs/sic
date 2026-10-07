"""Eurostat connector: JSON-stat decoding, compact tables, publication-date filtering, errors."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from sic.sources.base import VerifyContext
from sic.sources.eurostat import (
    API,
    DATASETS,
    Cube,
    EurostatConnector,
    fmt_val,
    period_status,
    publication_lag,
)
from sic.sources.insee import CachedHttp

FIX = Path(__file__).parent / "fixtures"
URL = f"{API}/gov_10dd_edpt1"


def edp_json() -> dict:
    return json.loads((FIX / "eurostat_gov_10dd_edpt1.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path) -> EurostatConnector:
    return EurostatConnector(http=CachedHttp(tmp_path, min_interval=0, retries=1, backoff=0))


def test_cube_decoding():
    cube = Cube.from_jsonstat(edp_json())
    assert cube.dims[-2:] == ["geo", "time"]
    assert cube.get({"freq": "A", "unit": "PC_GDP", "sector": "S13", "na_item": "GD", "geo": "FR", "time": "2021"})[0] == 112.8
    assert cube.get({"freq": "A", "unit": "PC_GDP", "sector": "S13", "na_item": "B9", "geo": "DE", "time": "2020"})[0] == -4.4


def test_fmt_val():
    assert fmt_val(0.8) == "0.8"
    assert fmt_val(1867.0) == "1 867"
    assert fmt_val(-6.6) == "-6.6"
    assert fmt_val(None) == "n.d."


def test_publication_lag_rules():
    assert publication_lag("gov_10dd_edpt1") == 112
    assert period_status("gov_10dd_edpt1", "2021", date(2022, 4, 20)) == "unpublished"
    assert period_status("gov_10dd_edpt1", "2021", date(2022, 4, 22)) == "ok"  # EDP notification day
    assert period_status("gov_10dd_edpt1", "2022", date(2022, 4, 20)) == "future"
    assert period_status("une_rt_m", "2022-03", date(2022, 4, 20)) == "unpublished"
    assert period_status("une_rt_m", "2022-02", date(2022, 4, 20)) == "ok"
    assert period_status("earn_mw_cur", "2022-S1", date(2022, 4, 20)) == "ok"  # known when it applies
    assert period_status("earn_mw_cur", "2022-S2", date(2022, 4, 20)) == "future"
    assert period_status("x", "2021", None) == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_get_with_as_of_hides_unpublished_year(conn):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=edp_json()))
    res = await conn.call("eurostat_get", {"dataset": "gov_10dd_edpt1", "since": "2018"}, VerifyContext(as_of=date(2022, 4, 20)))
    c = res.content
    req = route.calls.last.request
    assert req.url.params.get_list("geo") == ["FR", "DE", "EU27_2020"]
    assert req.url.params.get_list("na_item") == ["GD", "B9"]
    assert req.url.params["unit"] == "PC_GDP" and req.url.params["sector"] == "S13"
    # 2021 deficit/debt were notified on 22 April 2022: not yet published on the 20th
    assert "pas encore publiées à la date du débat: 2021" in c
    assert "\n2021:" not in c and "\n2022:" not in c
    assert "2020: 114.9 | 68 | 89.5" in c and "2020: -8.9 | -4.4 | -6.7" in c
    assert c.index("na_item=GD") < c.index("na_item=B9")  # requested order kept
    (src,) = res.sources
    assert src.publisher == "Eurostat" and src.kind == "official_data" and src.date == "2020"
    assert src.url == "https://ec.europa.eu/eurostat/databrowser/view/gov_10dd_edpt1/default/table"
    assert "FR 114.9" in src.excerpt
    assert len(c) < 4000


@pytest.mark.asyncio
@respx.mock
async def test_get_after_publication_and_filters_override(conn):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=edp_json()))
    res = await conn.call(
        "eurostat_get",
        {"dataset": "gov_10dd_edpt1", "geo": ["fr", "UE"], "filters": {"na_item": "GD"}, "since": "2018"},
        VerifyContext(as_of=date(2022, 4, 25)),
    )
    req = route.calls.last.request
    assert req.url.params.get_list("geo") == ["FR", "EU27_2020"]
    assert req.url.params.get_list("na_item") == ["GD"]
    assert "2021: 112.8" in res.content  # the mock returns both geos' data; FR/EU columns are shown
    res2 = await conn.call("eurostat_get", {"dataset": "gov_10dd_edpt1", "since": "2018"}, VerifyContext())
    assert "2025:" in res2.content and "Date du débat" not in res2.content


@pytest.mark.asyncio
@respx.mock
async def test_errors_are_returned_as_content(conn):
    respx.get(URL).mock(return_value=httpx.Response(400, text=(FIX / "eurostat_error.json").read_text()))
    res = await conn.call("eurostat_get", {"dataset": "gov_10dd_edpt1", "since": "abc"}, VerifyContext())
    assert "HTTP 400" in res.content and "sinceTimePeriod" in res.content and not res.sources
    res = await conn.call("eurostat_get", {"dataset": "nope"}, VerifyContext())
    assert "non pris en charge" in res.content
    respx.get(f"{API}/une_rt_a").mock(side_effect=httpx.ConnectError("down"))
    res = await conn.call("eurostat_get", {"dataset": "une_rt_a"}, VerifyContext())
    assert res.content.startswith("Erreur Eurostat") and not res.sources


@pytest.mark.asyncio
@respx.mock
async def test_unknown_filter_code_is_explained(conn):
    data = edp_json()
    i = data["id"].index("na_item")
    data["size"][i] = 0
    data["dimension"]["na_item"]["category"] = {"index": {}, "label": {}}
    data["value"] = {}
    respx.get(URL).mock(return_value=httpx.Response(200, json=data))
    res = await conn.call("eurostat_get", {"dataset": "gov_10dd_edpt1", "filters": {"na_item": "NOPE"}}, VerifyContext())
    assert "aucun code valide pour na_item=NOPE" in res.content


def test_tool_spec_lists_curated_datasets():
    (spec,) = EurostatConnector().tools()
    assert spec.name == "eurostat_get"
    assert spec.parameters["properties"]["dataset"]["enum"] == list(DATASETS)
    assert 15 <= len(DATASETS) <= 30
    for ds in DATASETS:
        assert ds in spec.description
    assert len(spec.description) < 9000


@pytest.mark.network
@pytest.mark.asyncio
async def test_real_api_debt_comparison(tmp_path):
    conn = EurostatConnector(http=CachedHttp(tmp_path, retries=2))
    res = await conn.call(
        "eurostat_get", {"dataset": "gov_10dd_edpt1", "since": "2020"}, VerifyContext(as_of=date(2022, 4, 25))
    )
    assert "2021: 112.8" in res.content or "2021: 112." in res.content, res.content
    assert res.sources and res.sources[0].date == "2021"
