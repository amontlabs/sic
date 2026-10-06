"""Eurostat connector: curated datasets for EU comparisons (JSON-stat 2.0 dissemination API).

API (open, no auth): https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}?geo=FR&...
One LLM tool, ``eurostat_get``, restricted to a curated list of datasets with sensible default
filters (see ``DATASETS``). Check them all against the API with
``uv run python -m sic.sources.eurostat verify``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any

from sic import config
from sic.models import Source
from sic.sources.base import ToolResult, ToolSpec, VerifyContext
from sic.sources.insee import CachedHttp, period_bounds

log = logging.getLogger(__name__)

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
CACHE_DIR = config.CACHE / "eurostat"
DATA_TTL = 24 * 3600
MAX_CONTENT = 3800
DEFAULT_GEO = ["FR", "DE", "EU27_2020"]

STATUS_FR = {"p": "provisoire", "e": "estimé", "b": "rupture de série", "d": "définition différente", "u": "peu fiable", "c": "confidentiel", "f": "prévision", "s": "estimation Eurostat"}


@dataclass
class Dataset:
    id: str
    label: str  # French label shown to the LLM
    defaults: dict[str, str | list[str]]
    doc: str  # filters / codes worth knowing (French)
    years: int = 8  # default history depth (in years) when `since` is omitted


# Conservative delay (days) between the end of a period and its first Eurostat publication. For a
# debate held on `as_of`, observations with period_end + lag > as_of are hidden ("pas encore
# publiées"). Negative values: data known before the period ends (e.g. minimum wages).
PUB_LAG_FREQ: dict[str, int] = {"M": 35, "Q": 60, "A": 110, "S": 120}
PUB_LAG_DAYS: dict[str, int] = {
    "gov_10dd_edpt1": 112,  # EDP notification ~22 April of the following year
    "gov_10a_main": 112,
    "gov_10a_taxag": 300,
    "gov_10a_exp": 410,  # COFOG ~February of year t+2
    "tec00115": 70,
    "tec00114": 170,
    "une_rt_m": 35,
    "une_rt_a": 120,
    "lfsi_emp_a": 120,
    "lfsa_ergan": 130,
    "prc_hicp_minr": 20,  # final HICP ~mid/late following month
    "prc_hicp_ainr": 20,
    "nrg_pc_204": 120,
    "nrg_pc_202": 120,
    "earn_mw_cur": -150,  # semester rates published ~1 month after the semester starts
    "ilc_li02": 180,
    "ilc_peps01n": 180,
    "spr_exp_pens": 520,
    "demo_gind": 200,
    "tps00199": 420,
    "migr_imm1ctz": 500,
    "migr_pop1ctz": 90,
    "migr_acq": 450,
    "migr_resfirst": 220,
    "migr_asyappctza": 80,
    "migr_eiord": 120,
    "migr_eirtn": 120,
}


def publication_lag(dataset: str, period: str = "") -> int:
    if dataset in PUB_LAG_DAYS:
        return PUB_LAG_DAYS[dataset]
    freq = "M" if re.fullmatch(r"\d{4}-\d{2}", period) else "Q" if "-Q" in period else "S" if "-S" in period else "A"
    return PUB_LAG_FREQ[freq]


def period_status(dataset: str, period: str, as_of: date | None) -> str:
    """'ok' (published at as_of), 'unpublished' (period over but not yet released) or 'future'."""
    if as_of is None:
        return "ok"
    try:
        end = period_bounds(period)[1]
    except ValueError:
        return "ok"
    if end + timedelta(days=publication_lag(dataset, period)) <= as_of:
        return "ok"
    return "unpublished" if end <= as_of else "future"


DATASETS: dict[str, Dataset] = {
    d.id: d
    for d in [
        Dataset(
            "gov_10dd_edpt1",
            "Dette et déficit publics au sens de Maastricht (annuel)",
            {"unit": "PC_GDP", "sector": "S13", "na_item": ["GD", "B9"]},
            "na_item: GD=dette brute consolidée, B9=solde public (capacité + / besoin - de financement = déficit), "
            "D41PAY=charge d'intérêts, B1GQ=PIB; unit: PC_GDP (% PIB) ou MIO_EUR; sector: S13=administrations publiques.",
        ),
        Dataset(
            "gov_10a_main",
            "Dépenses et recettes publiques totales (annuel)",
            {"unit": "PC_GDP", "sector": "S13", "na_item": ["TE", "TR"]},
            "na_item: TE=dépenses publiques totales, TR=recettes publiques totales, B9=solde, D1PAY=rémunération des "
            "agents publics, D62PAY=prestations sociales en espèces; unit: PC_GDP | MIO_EUR.",
        ),
        Dataset(
            "gov_10a_taxag",
            "Prélèvements obligatoires: impôts et cotisations sociales (annuel)",
            {"unit": "PC_GDP", "sector": "S13_S212", "na_item": "D2_D5_D91_D61_M_D995"},
            "na_item: D2_D5_D91_D61_M_D995=total impôts + cotisations sociales (taux de prélèvements obligatoires), "
            "D2_D5_D91=impôts seuls, D211=TVA, D51A=impôt sur le revenu des ménages, D51B=impôt sur les sociétés, "
            "D61=cotisations sociales; unit: PC_GDP | MIO_EUR.",
        ),
        Dataset(
            "gov_10a_exp",
            "Dépenses publiques par fonction COFOG (annuel)",
            {"unit": "PC_GDP", "sector": "S13", "na_item": "TE", "cofog99": ["GF10", "GF07", "GF09", "GF02", "GF03"]},
            "cofog99: TOTAL, GF01=services généraux, GF02=défense, GF03=ordre et sécurité publics, GF04=affaires "
            "économiques, GF05=environnement, GF06=logement, GF07=santé, GF08=culture, GF09=enseignement, "
            "GF10=protection sociale, GF1002=vieillesse (retraites); unit: PC_GDP | PC_TOT | MIO_EUR.",
        ),
        Dataset(
            "tec00115",
            "Croissance du PIB en volume (annuel, %)",
            {"unit": "CLV_PCH_PRE", "na_item": "B1GQ"},
            "unit: CLV_PCH_PRE=croissance du PIB réel, CLV_PCH_PRE_HAB=croissance du PIB réel par habitant.",
        ),
        Dataset(
            "tec00114",
            "PIB par habitant en standards de pouvoir d'achat (indice UE27=100)",
            {},
            "Indice de volume du PIB par habitant en SPA, UE27=100.",
        ),
        Dataset(
            "une_rt_m",
            "Taux de chômage mensuel (BIT, CVS)",
            {"s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT"},
            "age: TOTAL, Y_LT25 (moins de 25 ans), Y25-74; sex: T/M/F; unit: PC_ACT (% de la population active) "
            "| THS_PER (milliers).",
            years=2,
        ),
        Dataset(
            "une_rt_a",
            "Taux de chômage annuel (BIT)",
            {"age": "Y15-74", "sex": "T", "unit": "PC_ACT"},
            "age: Y15-74 (total), Y15-24 (jeunes), Y25-54, Y55-74; sex: T/M/F; unit: PC_ACT | THS_PER.",
        ),
        Dataset(
            "lfsi_emp_a",
            "Taux d'emploi (enquête forces de travail, annuel)",
            {"indic_em": "EMP_LFS", "sex": "T", "age": "Y20-64", "unit": "PC_POP"},
            "age: Y20-64 (objectif UE), Y15-64, Y15-24, Y55-64; sex: T/M/F; unit: PC_POP | THS_PER.",
        ),
        Dataset(
            "lfsa_ergan",
            "Taux d'emploi selon la nationalité (annuel)",
            {"citizen": ["TOTAL", "NAT", "EU27_2020_FOR", "NEU27_2020_FOR"], "age": "Y20-64", "sex": "T", "unit": "PC"},
            "citizen: TOTAL, NAT=nationaux, EU27_2020_FOR=autres pays UE, NEU27_2020_FOR=hors UE, FOR=étrangers; "
            "age: Y20-64, Y15-64; sex: T/M/F.",
        ),
        Dataset(
            "prc_hicp_minr",
            "Inflation mensuelle IPCH (glissement annuel, %)",
            {"unit": "RCH_A", "coicop18": "TOTAL"},
            "unit: RCH_A=glissement annuel, RCH_M=variation mensuelle, I15=indice 2015=100; coicop18: TOTAL, "
            "CP01=alimentation, CP045=énergie du logement (électricité, gaz), CP0722=carburants, NRG=énergie, "
            "SERV=services. Geo zone euro: EA.",
            years=2,
        ),
        Dataset(
            "prc_hicp_ainr",
            "Inflation annuelle moyenne IPCH (%)",
            {"unit": "RCH_A_AVG", "coicop18": "TOTAL"},
            "unit: RCH_A_AVG=taux d'inflation en moyenne annuelle, INX_A_AVG=indice moyen; coicop18: TOTAL, CP01 "
            "alimentation, NRG énergie.",
        ),
        Dataset(
            "nrg_pc_204",
            "Prix de l'électricité pour les ménages (semestriel, €/kWh)",
            {"siec": "E7000", "nrg_cons": "KWH2500-4999", "unit": "KWH", "tax": "I_TAX", "currency": "EUR"},
            "nrg_cons: KWH2500-4999 (ménage médian, bande DC), TOT_KWH (toutes bandes), KWH1000-2499, "
            "KWH5000-14999; tax: I_TAX=toutes taxes, X_TAX=hors taxes, X_VAT=hors TVA; currency: EUR | PPS.",
            years=4,
        ),
        Dataset(
            "nrg_pc_202",
            "Prix du gaz pour les ménages (semestriel, €/kWh)",
            {"siec": "G3000", "nrg_cons": "GJ20-199", "unit": "KWH", "tax": "I_TAX", "currency": "EUR"},
            "nrg_cons: GJ20-199 (bande D2, médiane), GJ_LT20, GJ_GE200, TOT_GJ; tax: I_TAX | X_TAX | X_VAT; "
            "currency: EUR | PPS.",
            years=4,
        ),
        Dataset(
            "earn_mw_cur",
            "Salaires minimums mensuels bruts (semestriel)",
            {"currency": "EUR"},
            "currency: EUR | PPS (standard de pouvoir d'achat) | NAC. Montants bruts mensuels; certains pays "
            "(Italie, Autriche, pays nordiques) n'ont pas de salaire minimum légal.",
            years=4,
        ),
        Dataset(
            "ilc_li02",
            "Taux de pauvreté monétaire (seuil 60 % du revenu médian, %)",
            {"rskpovth": "B_60", "statinfo": "MED_EI", "unit": "PC", "sex": "T", "age": "TOTAL"},
            "rskpovth: B_60 (seuil 60 %), B_50, B_40; age: TOTAL, Y_LT18 (enfants), Y_GE65; unit: PC | THS_PER.",
        ),
        Dataset(
            "ilc_peps01n",
            "Personnes menacées de pauvreté ou d'exclusion sociale (AROPE, %)",
            {"unit": "PC", "age": "TOTAL", "sex": "T"},
            "unit: PC | THS_PER; age: TOTAL, Y_LT18, Y_GE65.",
        ),
        Dataset(
            "spr_exp_pens",
            "Dépenses de retraites / pensions (% du PIB)",
            {"spdepb": "TOTAL", "spdepm": "TOTAL", "unit": "PC_GDP"},
            "spdepb: TOTAL, OLD=pensions de vieillesse, SRV=réversion, DIS=invalidité; unit: PC_GDP | MIO_EUR | PPS_HAB.",
        ),
        Dataset(
            "demo_gind",
            "Démographie: population, naissances, décès, solde migratoire (annuel)",
            {"indic_de": ["JAN", "LBIRTH", "DEATH", "CNMIGRAT"]},
            "indic_de: JAN=population au 1er janvier, LBIRTH=naissances vivantes, DEATH=décès, NATGROW=solde "
            "naturel, CNMIGRAT=solde migratoire (+ajustement), GBIRTHRT=taux de natalité ‰, GDEATHRT=taux de mortalité ‰.",
        ),
        Dataset(
            "tps00199",
            "Indicateur conjoncturel de fécondité (enfants par femme)",
            {},
            "Pas de filtre.",
        ),
        Dataset(
            "migr_imm1ctz",
            "Immigration: entrées annuelles selon la nationalité",
            {"citizen": ["TOTAL", "NAT", "EU27_2020_FOR", "NEU27_2020_FOR"], "agedef": "COMPLET", "age": "TOTAL", "sex": "T", "unit": "NR"},
            "citizen: TOTAL, NAT=nationaux de retour, EU27_2020_FOR=autres pays UE, NEU27_2020_FOR=hors UE (codes "
            "pays ISO aussi: DZ, MA, UA...).",
        ),
        Dataset(
            "migr_pop1ctz",
            "Population étrangère au 1er janvier (selon la nationalité)",
            {"citizen": ["TOTAL", "NAT", "EU27_2020_FOR", "NEU27_2020_FOR"], "age": "TOTAL", "sex": "T", "unit": "NR"},
            "citizen: TOTAL, NAT=nationaux, EU27_2020_FOR, NEU27_2020_FOR, FOR_STLS=étrangers et apatrides.",
        ),
        Dataset(
            "migr_acq",
            "Acquisitions de la nationalité (naturalisations)",
            {"citizen": "TOTAL", "agedef": "COMPLET", "age": "TOTAL", "sex": "T", "unit": "NR"},
            "citizen: TOTAL ou nationalité d'origine (codes pays).",
        ),
        Dataset(
            "migr_resfirst",
            "Premiers titres de séjour délivrés (par motif)",
            {"reason": ["TOTAL", "FAM", "EDUC", "EMP", "OTH"], "citizen": "TOTAL", "duration": "TOTAL", "unit": "PER"},
            "reason: TOTAL, FAM=familial, EDUC=études, EMP=travail, OTH=autres (dont humanitaire); citizen: TOTAL ou codes pays.",
        ),
        Dataset(
            "migr_asyappctza",
            "Demandes d'asile (annuel)",
            {"citizen": "TOTAL", "applicant": "FRST", "sex": "T", "age": "TOTAL", "unit": "PER"},
            "applicant: FRST=premières demandes, TOTAL; citizen: TOTAL, EXT_EU27_2020 ou codes pays (AF, SY...).",
        ),
        Dataset(
            "migr_eiord",
            "Étrangers (pays tiers) ayant reçu une obligation de quitter le territoire (OQTF)",
            {"citizen": "TOTAL", "age": "TOTAL", "sex": "T", "unit": "PER"},
            "citizen: TOTAL ou codes pays. À comparer avec migr_eirtn (retours effectifs).",
        ),
        Dataset(
            "migr_eirtn",
            "Étrangers (pays tiers) effectivement éloignés après une obligation de quitter le territoire",
            {"citizen": "TOTAL", "c_dest": "TOTAL", "age": "TOTAL", "sex": "T", "unit": "PER"},
            "citizen: TOTAL ou codes pays; c_dest: TOTAL | THRD (vers pays tiers).",
        ),
    ]
}

GEO_FR = {
    "EU27_2020": "UE27",
    "EA": "Zone euro",
    "EA20": "Zone euro (20)",
    "EA21": "Zone euro (21)",
}


# --------------------------------------------------------------------------------------
# JSON-stat decoding


@dataclass
class Cube:
    label: str
    updated: str
    dims: list[str]
    sizes: list[int]
    codes: dict[str, list[str]]  # dim -> ordered category codes
    labels: dict[str, dict[str, str]]
    values: dict[int, float]
    status: dict[int, str]

    @classmethod
    def from_jsonstat(cls, d: dict[str, Any]) -> Cube:
        dims = list(d["id"])
        sizes = list(d["size"])
        codes: dict[str, list[str]] = {}
        labels: dict[str, dict[str, str]] = {}
        for k in dims:
            cat = d["dimension"][k]["category"]
            idx = cat.get("index", {})
            if isinstance(idx, list):
                order = idx
            else:
                order = [c for c, _ in sorted(idx.items(), key=lambda x: x[1])] or list(cat.get("label", {}))
            codes[k] = order
            labels[k] = cat.get("label", {})
        raw = d.get("value", {})
        if isinstance(raw, list):
            values = {i: v for i, v in enumerate(raw) if v is not None}
        else:
            values = {int(i): v for i, v in raw.items() if v is not None}
        st = d.get("status", {}) or {}
        status = {i: s for i, s in enumerate(st)} if isinstance(st, list) else {int(i): s for i, s in st.items()}
        return cls(d.get("label", ""), d.get("updated", ""), dims, sizes, codes, labels, values, status)

    def get(self, coord: dict[str, str]) -> tuple[float | None, str]:
        idx = 0
        for k, n in zip(self.dims, self.sizes):
            pos = self.codes[k].index(coord[k])
            idx = idx * n + pos
        return self.values.get(idx), self.status.get(idx, "")


# --------------------------------------------------------------------------------------
# connector


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]


class EurostatConnector:
    name = "eurostat"

    def __init__(self, *, cache_dir: Path | None = None, ttl: float = DATA_TTL, http: CachedHttp | None = None) -> None:
        self.http = http or CachedHttp(cache_dir or CACHE_DIR, ttl=ttl, min_interval=0.3, timeout=60)

    def tools(self) -> list[ToolSpec]:
        lines = [f"- {d.id}: {d.label}. Défauts {_fmt_filters(d.defaults)}. {d.doc}" for d in DATASETS.values()]
        return [
            ToolSpec(
                name="eurostat_get",
                description=(
                    "Statistiques Eurostat pour comparer la France à d'autres pays européens (dette, déficit, "
                    "dépenses et prélèvements publics, chômage, emploi, inflation, croissance, prix de l'énergie, "
                    "SMIC européens, pauvreté, retraites, démographie, immigration, asile, OQTF). Renvoie un "
                    "tableau période × pays. Les filtres par défaut donnent l'indicateur principal; ne passer "
                    "'filters' que pour changer une dimension (valeur ou liste de codes). Codes pays: FR, DE, IT, "
                    "ES, NL, BE, PL, SE, PT, AT, IE, DK, FI, EL, EU27_2020 (UE), EA (zone euro).\nJeux de données:\n"
                    + "\n".join(lines)
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "dataset": {"type": "string", "enum": list(DATASETS)},
                        "geo": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": DEFAULT_GEO,
                            "description": "Codes pays Eurostat (max 12).",
                        },
                        "filters": {
                            "type": "object",
                            "description": "Surcharge des dimensions, ex. {\"na_item\": \"GD\", \"unit\": \"MIO_EUR\"}.",
                            "additionalProperties": {
                                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]
                            },
                        },
                        "since": {
                            "type": "string",
                            "description": "Première période, ex. '2015', '2021-01', '2019-S1'. Défaut: ~8 ans.",
                        },
                    },
                    "required": ["dataset"],
                },
            )
        ]

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        if tool != "eurostat_get":
            return ToolResult(content=f"Outil inconnu pour le connecteur Eurostat: {tool}")
        try:
            return await self.get(
                str(args.get("dataset", "")),
                geo=_as_list(args.get("geo")) or None,
                filters=args.get("filters") or None,
                since=args.get("since"),
                ctx=ctx,
            )
        except Exception as e:
            log.exception("eurostat call failed")
            return ToolResult(content=f"Erreur Eurostat ({type(e).__name__}): {e}")

    async def fetch(self, dataset: str, params: list[tuple[str, str]]) -> tuple[int, Any]:
        """GET a dataset; returns (status, json or error text)."""
        # httpx accepts repeated keys as list values
        grouped: dict[str, list[str]] = {}
        for k, v in params:
            grouped.setdefault(k, []).append(v)
        status, body = await self.http.get(f"{API}/{dataset}", grouped)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return status, body[:300]
        return status, data

    async def get(
        self,
        dataset: str,
        *,
        geo: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        since: str | None = None,
        ctx: VerifyContext,
    ) -> ToolResult:
        ds = DATASETS.get(dataset)
        if ds is None:
            return ToolResult(content=f"Jeu de données Eurostat non pris en charge: {dataset!r}. Choix: {', '.join(DATASETS)}.")
        geos = [g.strip().upper() for g in (geo or DEFAULT_GEO) if g and g.strip()][:12]
        geos = ["EU27_2020" if g in ("EU", "UE", "EU27") else "EL" if g == "GR" else "UK" if g == "GB" else g for g in geos]
        flt: dict[str, list[str]] = {k: _as_list(v) for k, v in ds.defaults.items()}
        for k, v in (filters or {}).items():
            if k in ("geo", "time", "lang"):
                continue
            vals = _as_list(v)
            if vals:
                flt[k] = vals
        as_of = ctx.as_of
        ref_year = (as_of or datetime.now(UTC).date()).year
        since_s = str(since).strip() if since else str(ref_year - ds.years)
        params: list[tuple[str, str]] = [("lang", "en"), ("sinceTimePeriod", since_s)]
        params += [("geo", g) for g in geos]
        for k, vals in flt.items():
            params += [(k, v) for v in vals]

        status, data = await self.fetch(ds.id, params)
        if status != 200 or not isinstance(data, dict) or "value" not in data:
            err = ""
            if isinstance(data, dict):
                errs = data.get("error")
                if isinstance(errs, list) and errs:
                    err = "; ".join(str(e.get("label", e)) for e in errs)
                elif isinstance(errs, dict):
                    err = str(errs.get("label", errs))
            else:
                err = str(data)
            return ToolResult(
                content=f"Eurostat {ds.id}: requête refusée (HTTP {status}) {err[:300]}\n"
                f"Filtres utilisés: {_fmt_filters(flt)} | geo={geos} | since={since_s}.\n"
                f"Filtres connus: {ds.doc}"
            )
        empty = [k for k, n in zip(data.get("id", []), data.get("size", [])) if n == 0 and k != "time"]
        if empty:
            bad = "; ".join(f"{k}={'|'.join(flt.get(k, geos if k == 'geo' else []))}" for k in empty)
            return ToolResult(
                content=f"Eurostat {ds.id}: aucun code valide pour {bad}. Filtres connus: {ds.doc}"
            )
        cube = Cube.from_jsonstat(data)
        content, last_period, excerpt = render(cube, ds, geos, as_of, order=flt)
        source = Source(
            kind="official_data",
            publisher="Eurostat",
            title=f"Eurostat — {cube.label or ds.label} ({ds.id})",
            url=f"https://ec.europa.eu/eurostat/databrowser/view/{ds.id}/default/table",
            date=last_period,
            excerpt=excerpt[:300] if excerpt else None,
        )
        return ToolResult(content=content, sources=[source] if last_period else [])


def fmt_val(v: float | None) -> str:
    """Eurostat values as published: 0.8 -> '0.8', 1867.0 -> '1 867', 112.75 -> '112.75'."""
    if v is None:
        return "n.d."
    if float(v).is_integer():
        return f"{int(v):,}".replace(",", " ")
    if abs(v) >= 1000:
        return f"{v:,.1f}".replace(",", " ")
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _fmt_filters(f: dict[str, Any]) -> str:
    if not f:
        return "{}"
    return "{" + ", ".join(f"{k}={'|'.join(_as_list(v))}" for k, v in f.items()) + "}"


def render(
    cube: Cube, ds: Dataset, geos: list[str], as_of: date | None, order: dict[str, list[str]] | None = None
) -> tuple[str, str | None, str]:
    """Compact table(s): one block per combination of multi-valued dimensions, rows=periods, cols=geo."""
    times_all = cube.codes.get("time", [])
    status = {t: period_status(ds.id, t, as_of) for t in times_all}
    times = [t for t in times_all if status[t] == "ok"]
    unpublished = [t for t in times_all if status[t] == "unpublished"]
    n_future = sum(1 for t in times_all if status[t] == "future")
    geo_cols = [g for g in geos if g in cube.codes.get("geo", [])]
    missing_geo = [g for g in geos if g not in geo_cols]
    other = [k for k in cube.dims if k not in ("geo", "time")]
    fixed = {k: cube.codes[k][0] for k in other if len(cube.codes[k]) == 1}
    multi = [k for k in other if len(cube.codes[k]) > 1]

    head = [f"Eurostat {ds.id} — {cube.label or ds.label} (MAJ {cube.updated[:10]})"]
    fixed_desc = [f"{k}={v} ({cube.labels[k].get(v, v)[:60]})" for k, v in fixed.items() if k != "freq"]
    if fixed_desc:
        head.append("Filtres: " + "; ".join(fixed_desc))
    if as_of is not None:
        lag = publication_lag(ds.id, times_all[-1] if times_all else "")
        lag_txt = f"{lag} j après la fin de la période" if lag >= 0 else "publication en cours de période"
        msg = f"Date du débat {as_of}: seules les données déjà publiées sont montrées (délai de publication estimé: {lag_txt})."
        if unpublished:
            msg += f" Exclues car pas encore publiées à la date du débat: {', '.join(unpublished[-4:])}."
        if n_future:
            msg += f" {n_future} période(s) postérieure(s) exclue(s)."
        head.append(msg + " Valeurs = dernières données révisées, pas forcément celles publiées à l'époque.")
    if missing_geo:
        head.append(f"Pas de données pour: {', '.join(missing_geo)}")
    head.append("Colonnes: " + " | ".join(f"{g}={GEO_FR.get(g, cube.labels['geo'].get(g, g))}" for g in geo_cols))

    blocks: list[list[str]] = []
    last_period: str | None = None
    excerpt = ""
    flags_seen: set[str] = set()

    def ordered(k: str) -> list[str]:
        wanted = (order or {}).get(k) or []
        return [c for c in wanted if c in cube.codes[k]] + [c for c in cube.codes[k] if c not in wanted]

    combos = list(product(*[ordered(k) for k in multi])) if multi else [()]
    for combo in combos:
        coord_base = dict(fixed)
        coord_base.update(dict(zip(multi, combo)))
        rows = []
        for t in times:
            cells, any_val = [], False
            for g in geo_cols:
                v, st = cube.get({**coord_base, "geo": g, "time": t})
                if v is not None:
                    any_val = True
                    flags_seen.update(st)
                cells.append(fmt_val(v) + (st if v is not None else ""))
            if any_val:
                rows.append(f"{t}: " + " | ".join(cells))
                last = t
        if not rows:
            continue
        title = ", ".join(f"{k}={c} ({cube.labels[k].get(c, c)[:70]})" for k, c in zip(multi, combo))
        blocks.append(([f"[{title}]"] if title else []) + rows)
        if last_period is None or _period_sort(last) > _period_sort(last_period):
            last_period = last
        if not excerpt:
            vals = []
            for g in geo_cols:
                v, _ = cube.get({**coord_base, "geo": g, "time": last})
                if v is not None:
                    vals.append(f"{g} {fmt_val(v)}")
            excerpt = f"{last}{' ' + title if title else ''}: " + ", ".join(vals)

    if not blocks:
        return "\n".join(head + ["Aucune valeur disponible pour ces filtres/pays/périodes."]), None, ""
    tail = []
    if flags_seen & set(STATUS_FR):
        tail.append("Drapeaux: " + ", ".join(f"{f}={STATUS_FR[f]}" for f in sorted(flags_seen) if f in STATUS_FR))
    # fit within budget: drop the oldest rows of every block until it fits
    while True:
        text = "\n".join(head + [line for b in blocks for line in b] + tail)
        if len(text) <= MAX_CONTENT:
            break
        trimmed = False
        for b in blocks:
            data_start = 1 if b and b[0].startswith("[") else 0
            if len(b) - data_start > 2:
                del b[data_start]
                trimmed = True
        if not trimmed:
            text = text[: MAX_CONTENT - 20] + "\n[… tronqué …]"
            break
    return text, last_period, excerpt


def _period_sort(p: str) -> date:
    try:
        return period_bounds(p)[0]
    except ValueError:
        return date.min


# --------------------------------------------------------------------------------------
# CLI


async def verify(geo: list[str] | None = None) -> bool:
    conn = EurostatConnector()
    ok = True
    for ds in DATASETS.values():
        res = await conn.get(ds.id, geo=geo or DEFAULT_GEO, ctx=VerifyContext())
        good = bool(res.sources)
        ok &= good
        first = res.content.splitlines()
        print(f"{'ok  ' if good else 'FAIL'} {ds.id:18s} {len(res.content):5d} chars  {res.sources[0].excerpt if good else first[-1]}")
    return ok


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m sic.sources.eurostat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="query every curated dataset with its default filters")
    g = sub.add_parser("get")
    g.add_argument("dataset")
    g.add_argument("--geo", nargs="*")
    g.add_argument("--filters", help="JSON object")
    g.add_argument("--since")
    g.add_argument("--as-of")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.cmd == "verify":
        return 0 if asyncio.run(verify()) else 1
    ctx = VerifyContext(as_of=date.fromisoformat(args.as_of) if args.as_of else None)
    res = asyncio.run(
        EurostatConnector().call(
            "eurostat_get",
            {"dataset": args.dataset, "geo": args.geo, "filters": json.loads(args.filters) if args.filters else None, "since": args.since},
            ctx,
        )
    )
    print(res.content)
    for s in res.sources:
        print("SOURCE:", s.model_dump_json(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
