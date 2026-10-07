"""INSEE connector: macro-economic series from the BDM (Banque de données macro-économiques).

The BDM SDMX API is open (no auth): https://api.insee.fr/series/BDM/V1/ . Two LLM tools:

* ``insee_search_series``: BM25 search over a pre-built catalog of ~series titles
  (``data/sources/insee/catalog.jsonl.gz``) plus a hand-curated list of key indicators
  (``data/sources/insee/key_series.json``).
* ``insee_get_series``: observations of one series (by idbank), filtered by ``ctx.as_of``,
  with derived numbers (change vs previous period / one year, annual averages).

Rebuild the catalog with ``uv run python -m sic.sources.insee build-catalog``.
Check the curated key series with ``uv run python -m sic.sources.insee verify-keys``.
"""

from __future__ import annotations

import asyncio
import calendar
import gzip
import hashlib
import json
import logging
import math
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from rank_bm25 import BM25Okapi

from sic import config
from sic.models import Source
from sic.sources.base import ToolResult, ToolSpec, VerifyContext

log = logging.getLogger(__name__)

API = "https://api.insee.fr/series/BDM/V1"
DATA_DIR = config.DATA / "sources" / "insee"
CATALOG_PATH = DATA_DIR / "catalog.jsonl.gz"
KEY_SERIES_PATH = DATA_DIR / "key_series.json"
CACHE_DIR = config.CACHE / "insee"

DATA_TTL = 24 * 3600  # observations: refreshed daily
BUILD_TTL = 7 * 24 * 3600  # catalog harvesting
MAX_CONTENT = 3800

# Dataflows harvested into the catalog. Current bases first; a few stopped bases are kept
# because debates often refer to past figures. Huge / irrelevant flows are skipped.
CATALOG_DATAFLOWS: list[str] = [
    # Labour market
    "CHOMAGE-TRIM-NATIONAL",
    "EMPLOI-BIT-TRIM",
    "EMPLOI-SALARIE-TRIM-NATIONAL",
    "DEMANDES-EMPLOIS-NATIONALES",
    "TAUX-CHOMAGE",
    "ERI-ACTIVITE-PARTIELLE",
    # Prices
    "IPC-2025",
    "IPC-2015",
    "IPCH-2025",
    "IPCH-2015",
    "IPC-PM-2015",
    "IRL",
    "INDICES_LOYERS",
    "IPLA-IPLNA-2015",
    "IPPMP",
    # Public finances
    "DETTE-TRIM-APU-2020",
    "DETTE-TRIM-APU-2014",
    "CNA-2014-DETTE-APU",
    "CNA-2014-DEP-APU",
    "COMPTES-ETAT",
    "DETTE-NEGOCIABLE-ETAT",
    # National accounts
    "CNA-2020-PIB",
    "CNA-2014-PIB",
    "CNA-2020-CSI",
    "CNA-2014-CSI",
    "CNA-2014-RDB",
    "CNA-2020-EMPLOI",
    "CNT-2020-PIB-EQB-RF",
    "CNT-2020-CSI",
    "CNT-2020-OPERATIONS",
    "CONSO-MENAGES-2020",
    # Trade, business
    "COM-EXT",
    "BALANCE-PAIEMENTS",
    "DEFAILLANCES-ENTREPRISES",
    "CREATIONS-ENTREPRISES-METHODE-2022",
    "IPI-2021",
    "CLIMAT-AFFAIRES",
    "ENQ-CONJ-MENAGES",
    # Wages, income
    "SMIC-COTISATIONS",
    "SALAIRES-ACEMO-2017",
    "SALAIRES-ANNUELS",
    "INDICE-TRAITEMENT-FP",
    "ICT-2020",
    # Demography
    "NAISSANCES-FECONDITE",
    "DECES-MORTALITE",
    "POPULATION-STRUCTURE",
    "MARIAGES-NUPTIALITE-PACS-DIVORCES",
    # Housing
    "CONSTRUCTION-LOGEMENTS",
    "PARC-LOGEMENTS",
    # Transport
    "TRANSPORTS",
    # Sustainable development goals (poverty, inequality, education, health, energy...)
    "ODD-CONSOMMATION-PRODUCTION",
    "ODD-EAU-PROPRE-ASSAINISSEMENT",
    "ODD-EDUCATION-QUALITE",
    "ODD-EGALITE-SEXES",
    "ODD-ENERGIE-PROPRE-COUT-ABORDABLE",
    "ODD-ERADICATION-PAUVRETE",
    "ODD-INFRASTRUCTURES-RESILIENTES-INNOVATION",
    "ODD-LUTTE-CHANGEMENT-CLIMATIQUE",
    "ODD-PAIX-JUSTICE-INSTITUTIONS-EFFICACES",
    "ODD-PARTENARIATS-REALISATION",
    "ODD-REDUCTION-INEGALITES",
    "ODD-SANTE-BIEN-ETRE",
    "ODD-SECURITE-ALIMENTAIRE-AGRICULTURE-DURABLE",
    "ODD-TRAVAIL-CROISSANCE",
    "ODD-VIE-TERRESTRE",
    "ODD-VILLES-COMMUNAUTES-DURABLES",
    # Regional / local tables (useful for "dans ma région", public employment, prisons...)
    "TCRED-CONDITIONSDEVIE-APE-PEC",
    "TCRED-CONDITIONSDEVIE-LOG-SOC",
    "TCRED-ESTIMATIONS-POPULATION",
    "TCRED-SALAIRES-REVENUS-TAUX-PAUVRETE-AGE",
    "TCRED-SALAIRES-REVENUS-RET-BEN",
    "TCRED-SALAIRES-REVENUS-TNB",
    "TCRED-TRAVAIL-EMPLOI-EFF-FP-RATIO",
    "TCRED-TRAVAIL-EMPLOI-EFF-FPE",
    "TCRED-TRAVAIL-EMPLOI-EFF-FPH",
    "TCRED-TRAVAIL-EMPLOI-EFF-FPT",
    "TCRED-SANTE-PERSONNELS",
    "TCRED-EDUCATION-ENS-PSD",
    "TCRED-SANTE-EQUIP-HOP",
    "TCRED-CONDITIONSDEVIE-TXP-CDE",
    "TCRED-ECONOMIE-PIB-REG",
    "TCRED-EMPLOI-SALARIE-TRIM",
    # extra national detail
    "CNT-2020-CB",
    "CNA-2020-CONSO-MEN",
    "IPAGRI-BASE-2020",
]

FREQ_FR = {
    "A": "annuelle",
    "T": "trimestrielle",
    "M": "mensuelle",
    "S": "semestrielle",
    "B": "bimestrielle",
    "J": "journalière",
}
# number of periods in one year, per frequency
FREQ_LAG = {"A": 1, "T": 4, "M": 12, "S": 2, "B": 6}

UNIT_FR = {
    "POURCENT": "%",
    "EUROS": "€",
    "EUR": "€",
    "EUROS_COURANTS": "€",
    "EUR2020": "€ (prix chaînés 2020)",
    "EUROS_HABITANTS": "€ par habitant",
    "EUROS_METRE_CARRE": "€/m²",
    "MILLIONS_EUROS": "millions d'euros",
    "MILLIARDS_EUROS": "milliards d'euros",
    "NOMBRE": "nombre",
    "UNITE": "nombre",
    "INDIVIDUS": "personnes",
    "ENFANTS_NES_VIVANTS": "enfants",
    "P10000": "pour 10 000",
    "P1000": "pour 1 000",
    "E1000": "pour 1 000",
    "ANNEES": "années",
    "ETP": "équivalents temps plein",
    "SO": "",
}
MULT_FR = {"3": "milliers", "6": "millions", "9": "milliards", "-2": "centièmes"}


# --------------------------------------------------------------------------------------
# text helpers


def fold(text: str) -> str:
    """Lowercase + strip accents."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


_STOP = {
    "de", "des", "du", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux", "un", "une",
    "par", "pour", "sur", "dans", "ou", "y", "compris", "the", "of", "est", "quel", "quelle",
    "combien", "france",  # "france" is handled by the prior (nearly every series has it)
}


def _stem(tok: str) -> str:
    if len(tok) > 4 and tok.endswith(("s", "x")) and not tok.endswith("ss"):
        tok = tok[:-1]
    if len(tok) > 5 and tok.endswith("e"):
        tok = tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", fold(text))
    return [_stem(t) for t in toks if t not in _STOP]


# Query expansion: common debate wording -> words used in INSEE titles.
SYNONYMS: dict[str, str] = {
    "inflation": "indice prix consommation glissement annuel ensemble",
    "prix": "indice prix",
    "chomeur": "chomage demandeurs emploi",
    "chomeurs": "chomage demandeurs emploi",
    "chomage": "chomage bit",
    "pole emploi": "demandeurs emploi",
    "france travail": "demandeurs emploi",
    "dette publique": "dette maastricht administrations publiques",
    "dette": "dette maastricht",
    "deficit public": "deficit maastricht administrations publiques besoin financement",
    "deficit": "deficit besoin financement",
    "croissance": "produit interieur brut pib volume evolution",
    "pib": "produit interieur brut",
    "pouvoir d'achat": "pouvoir achat revenu disponible brut",
    "pouvoir dachat": "pouvoir achat revenu disponible brut",
    "smic": "smic salaire minimum horaire brut",
    "salaire minimum": "smic",
    "balance commerciale": "solde commercial echanges exterieurs",
    "deficit commercial": "solde commercial echanges exterieurs",
    "commerce exterieur": "echanges exterieurs exportations importations",
    "faillites": "defaillances entreprises",
    "faillite": "defaillances entreprises",
    "natalite": "naissances",
    "fecondite": "indicateur conjoncturel fecondite",
    "mortalite": "deces",
    "logements": "logements construction",
    "mises en chantier": "logements commences",
    "permis de construire": "logements autorises",
    "electricite": "electricite",
    "carburant": "gazole essence carburants",
    "essence": "carburants",
    "gazole": "carburants",
    "diesel": "gazole",
    "loyer": "loyers",
    "pauvrete": "pauvrete taux",
    "fonctionnaires": "fonction publique effectifs",
    "prisons": "population ecrouee detenus",
    "detenus": "population ecrouee",
    "budget de l'etat": "budget etat solde execution",
    "epargne": "taux epargne menages",
    "emploi": "emploi",
    "salaires": "salaire",
    "retraites": "retraites pensions",
    "jeunes": "moins de 25 ans 15 a 24 ans",
    "seniors": "55 a 64 ans",
    "immigration": "solde migratoire",
    "immigres": "solde migratoire",
}


def expand_query(query: str) -> str:
    q = fold(query).replace("’", "'")
    extra = [exp for key, exp in SYNONYMS.items() if re.search(rf"\b{re.escape(key)}\b", q)]
    return " ".join([q, *extra])


# --------------------------------------------------------------------------------------
# periods


def period_bounds(period: str) -> tuple[date, date]:
    """Start and end date of an INSEE TIME_PERIOD ("2021", "2021-Q4", "2021-03", "2021-S1"...)."""
    p = period.strip()
    if m := re.fullmatch(r"(\d{4})", p):
        y = int(m[1])
        return date(y, 1, 1), date(y, 12, 31)
    if m := re.fullmatch(r"(\d{4})-(\d{2})", p):
        y, mo = int(m[1]), int(m[2])
        return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return d, d
    if m := re.fullmatch(r"(\d{4})-([QTSB])(\d{1,2})", p):
        y, kind, n = int(m[1]), m[2], int(m[3])
        months = {"Q": 3, "T": 3, "S": 6, "B": 2}[kind]
        start_m = (n - 1) * months + 1
        end_m = start_m + months - 1
        return date(y, start_m, 1), date(y, end_m, calendar.monthrange(y, end_m)[1])
    if m := re.fullmatch(r"(\d{4})-W(\d{2})", p):
        start = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        return start, start + timedelta(days=6)
    raise ValueError(f"unrecognised period {period!r}")


def _period_key(period: str) -> date:
    try:
        return period_bounds(period)[0]
    except ValueError:
        return date.min


def parse_bound(value: str | None, *, end: bool) -> date | None:
    """User-supplied start/end ("2020", "2020-03", "2020-Q2", "2020-03-15")."""
    if not value:
        return None
    try:
        s, e = period_bounds(str(value))
    except ValueError:
        return None
    return e if end else s


# --------------------------------------------------------------------------------------
# HTTP with disk cache, retries and rate limiting


class CachedHttp:
    """Tiny GET client: disk cache with TTL, polite rate limiting, retries on 429/5xx."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl: float = DATA_TTL,
        min_interval: float = 0.7,
        timeout: float = 30.0,
        retries: int = 4,
        backoff: float = 1.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.backoff = backoff
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.headers = headers or {"User-Agent": "sic-factcheck/0.1 (+https://github.com)"}
        self._locks: dict[int, asyncio.Lock] = {}  # one per event loop
        self._last = 0.0

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".cache")

    async def get(
        self, url: str, params: dict[str, Any] | None = None, *, ttl: float | None = None
    ) -> tuple[int, str]:
        """Return (status, body). Status 200 and 404 bodies are cached."""
        ttl = self.ttl if ttl is None else ttl
        path = self._path(url, params)
        if path.exists() and (ttl < 0 or time.time() - path.stat().st_mtime < ttl):
            status_s, _, body = path.read_text(encoding="utf-8").partition("\n")
            return int(status_s), body
        last_err: str = ""
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            for attempt in range(self.retries + 1):
                lock = self._locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
                async with lock:
                    wait = self.min_interval - (time.monotonic() - self._last)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last = time.monotonic()
                try:
                    resp = await client.get(url, params=params)
                except httpx.HTTPError as e:
                    last_err = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(min(2**attempt, 20) * self.backoff)
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    retry_after = resp.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1)
                    await asyncio.sleep(min(delay, 60) * self.backoff)
                    continue
                body = resp.text
                if resp.status_code in (200, 404):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(f"{resp.status_code}\n{body}", encoding="utf-8")
                    tmp.replace(path)
                return resp.status_code, body
        # network failure: fall back to a stale cache entry if any
        if path.exists():
            status_s, _, body = path.read_text(encoding="utf-8").partition("\n")
            return int(status_s), body
        raise ConnectionError(f"GET {url} failed after {self.retries + 1} attempts ({last_err})")


# --------------------------------------------------------------------------------------
# SDMX parsing


@dataclass
class Series:
    idbank: str
    title: str
    freq: str
    attrs: dict[str, str] = field(default_factory=dict)
    obs: list[tuple[str, float | None]] = field(default_factory=list)  # ascending by period

    @property
    def unit(self) -> str:
        return unit_label(self.attrs.get("UNIT_MEASURE", ""), self.attrs.get("UNIT_MULT", "0"), self.title)

    @property
    def decimals(self) -> int | None:
        try:
            return int(self.attrs["DECIMALS"])
        except (KeyError, ValueError):
            return None


def unit_label(unit: str, mult: str = "0", title: str = "") -> str:
    base = UNIT_FR.get(unit, unit.lower().replace("_", " ") if unit else "")
    if not base and title:
        t = fold(title)
        if "taux" in t or "en %" in t or "pourcentage" in t or "glissement" in t or "variation" in t:
            base = "%"
        elif t.startswith("indice") or " indice " in t:
            base = "indice"
    m = MULT_FR.get(str(mult))
    if m:
        if base == "€":
            return f"{m} d'euros"
        return f"{m} ({base})" if base else m
    return base


def _float(v: str | None) -> float | None:
    if v is None or v == "" or v.lower() == "nan":
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return None if math.isnan(x) else x


def parse_sdmx(xml_text: str) -> list[Series]:
    """Parse a StructureSpecificData message into Series (observations sorted ascending)."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out: list[Series] = []
    for s in root.iter():
        if s.tag.rsplit("}", 1)[-1] != "Series":
            continue
        attrs = dict(s.attrib)
        obs: list[tuple[str, float | None]] = []
        for o in s:
            if o.tag.rsplit("}", 1)[-1] == "Obs" and "TIME_PERIOD" in o.attrib:
                obs.append((o.attrib["TIME_PERIOD"], _float(o.attrib.get("OBS_VALUE"))))
        obs.sort(key=lambda x: _period_key(x[0]))
        out.append(
            Series(
                idbank=attrs.get("IDBANK", ""),
                title=attrs.get("TITLE_FR", attrs.get("TITLE_EN", "")),
                freq=attrs.get("FREQ", ""),
                attrs=attrs,
                obs=obs,
            )
        )
    return out


def parse_error(xml_text: str) -> tuple[str | None, list[str]]:
    """Return (code, texts) for an SDMX error message, or (None, [])."""
    if "ErrorMessage" not in xml_text[:2000]:
        return None, []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return None, []
    code, texts = None, []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "ErrorMessage":
            code = el.attrib.get("code")
        elif tag == "Text" and el.text:
            texts.append(el.text.strip())
    return code, texts


# --------------------------------------------------------------------------------------
# value formatting / derived numbers


def fmt_num(x: float | None, decimals: int | None = None) -> str:
    if x is None:
        return "n.d."
    if decimals is None:
        ax = abs(x)
        decimals = 0 if ax >= 1000 else 1 if ax >= 100 else 2 if ax >= 1 else 3
        if float(x).is_integer() and ax >= 1:
            decimals = 0
    s = f"{x:,.{decimals}f}".replace(",", " ")
    return s


def series_kind(s: Series) -> str:
    """'index' (levels where % change matters), 'rate' (%, changes in points) or 'level'."""
    t = fold(s.title)
    unit = s.attrs.get("UNIT_MEASURE", "")
    nature = s.attrs.get("NATURE", "")
    if unit == "POURCENT" or nature.startswith(("VARIATION", "CONTRIB", "TAUX", "GLISSEMENT", "POND")):
        return "rate"
    if re.search(r"\b(taux|glissement|variation|evolution|contribution|en %|en point|en proportion|part)\b", t):
        return "rate"
    if t.startswith("indice") or nature == "INDICE" or " indice " in t:
        return "index"
    return "level"


def _annual_means(obs: list[tuple[str, float | None]], freq: str, *, total: bool = False) -> dict[int, float]:
    """Annual mean (or total) over complete years only."""
    per_year = FREQ_LAG.get(freq)
    if not per_year or per_year == 1:
        return {}
    acc: dict[int, list[float]] = {}
    for p, v in obs:
        if v is not None:
            acc.setdefault(_period_key(p).year, []).append(v)
    return {y: (sum(vs) if total else sum(vs) / len(vs)) for y, vs in acc.items() if len(vs) == per_year}


_FLOW = re.compile(
    r"(naissances|deces|defaillances|logements commences|logements autorises|creations d.entreprises|"
    r"prix courants|volume aux prix|solde fab|solde caf|exportations|importations|mariages|"
    r"capacite \(\+\) ou besoin)"
)


def is_flow(title: str) -> bool:
    """Flow series (sum over the year is meaningful), excluding cumulated / rate / index series."""
    t = fold(title)
    return bool(_FLOW.search(t)) and not re.search(r"cumul|taux|indice|evolution|glissement|contribution", t)


def obs_end(period: str, title: str = "") -> date:
    """Date at which an observation is complete. Stocks 'au 1er janvier' are known at the period start."""
    start, end = period_bounds(period)
    return start if "1er janvier" in fold(title) else end


# Conservative publication delays (days after the end of the observed period) used to hide, for a
# debate held on `as_of`, figures that INSEE had not yet published. Default by frequency, overridden
# per dataflow (prefix match, longest first) where the release calendar is known.
PUB_LAG_FREQ: dict[str, int] = {"M": 28, "T": 45, "A": 90, "S": 90, "B": 45, "J": 3}
PUB_LAG_DATAFLOW: dict[str, int] = {
    "CHOMAGE-TRIM-NATIONAL": 45,  # ILO unemployment: ~mid second month after the quarter
    "EMPLOI-BIT-TRIM": 45,
    "EMPLOI-SALARIE-TRIM-NATIONAL": 65,  # flash (private) ~40 d, full estimate ~65 d
    "DEMANDES-EMPLOIS-NATIONALES": 30,
    "TAUX-CHOMAGE": 75,
    "IPC-": 15,  # final CPI mid next month (provisional estimate at month end)
    "IPCH-": 15,
    "IPC-PM-": 15,
    "IPPMP": 15,
    "DETTE-TRIM-APU": 85,  # quarterly Maastricht debt ~end of third month
    "CNA-2014-DETTE-APU": 90,  # annual deficit/debt notified end of March
    "CNA-": 145,  # annual national accounts end of May
    "CNT-2020-PIB-EQB-RF": 30,  # quarterly GDP first estimate ~30 d
    "CNT-": 60,  # detailed quarterly accounts (sector accounts, APU balance)
    "COMPTES-ETAT": 45,
    "DETTE-NEGOCIABLE-ETAT": 30,
    "COM-EXT": 40,
    "BALANCE-PAIEMENTS": 45,
    "DEFAILLANCES-ENTREPRISES": 60,
    "CREATIONS-ENTREPRISES": 15,
    "SMIC-COTISATIONS": 0,  # set by decree before it applies
    "INDICE-TRAITEMENT-FP": 0,
    "SALAIRES-ACEMO": 60,
    "SALAIRES-ANNUELS": 330,
    "NAISSANCES-FECONDITE": 30,  # monthly births ~30 d; annual demographic report mid-January
    "DECES-MORTALITE": 30,
    "POPULATION-STRUCTURE": 20,
    "MARIAGES-NUPTIALITE": 30,
    "CONSTRUCTION-LOGEMENTS": 30,
    "PARC-LOGEMENTS": 180,
    "CLIMAT-AFFAIRES": 0,
    "ENQ-CONJ-": 0,
    "IPI-": 40,
    "CONSO-MENAGES-": 30,
    "ICT-": 75,
    "IRL": 15,
    "INDICES_LOYERS": 60,
    "IPLA-IPLNA": 60,
    "TRANSPORTS": 60,
    "ERI-ACTIVITE-PARTIELLE": 60,
    "ODD-ERADICATION-PAUVRETE": 600,  # poverty rate of year t published autumn t+2
    "ODD-": 365,
    "TCRED-": 365,
}
_ANNUAL_DEMO = ("NAISSANCES-FECONDITE", "DECES-MORTALITE", "POPULATION-STRUCTURE", "MARIAGES-NUPTIALITE")


def publication_lag(freq: str, dataflow: str = "") -> int:
    if freq == "A" and dataflow.startswith(_ANNUAL_DEMO):
        return 20  # bilan démographique, mid-January
    for prefix in sorted(PUB_LAG_DATAFLOW, key=len, reverse=True):
        if dataflow.startswith(prefix):
            return PUB_LAG_DATAFLOW[prefix]
    return PUB_LAG_FREQ.get(freq, 90)


def year_ago(period: str) -> str | None:
    m = re.match(r"(\d{4})(.*)", period)
    return f"{int(m[1]) - 1}{m[2]}" if m else None


# --------------------------------------------------------------------------------------
# catalog


@dataclass
class CatalogEntry:
    id: str
    t: str  # title (fr)
    f: str = ""  # freq
    u: str = ""  # unit
    m: str = "0"  # unit mult
    df: str = ""  # dataflow
    first: str = ""
    last: str = ""
    lu: str = ""  # last update
    area: str = ""
    corr: str = ""  # BRUT / CVS / CVS-CJO
    stop: bool = False
    nat: str = ""  # NATURE dimension
    key: str = ""  # description if curated key series
    boost: float = 1.0  # extra ranking weight for curated key series

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def load_key_series(path: Path = KEY_SERIES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["series"]


def load_catalog(path: Path = CATALOG_PATH) -> list[CatalogEntry]:
    if not path.exists():
        return []
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(CatalogEntry.from_dict(json.loads(line)))
    return out


_REGIONAL = re.compile(
    r"\b(departement|region|metropolitaine|guadeloupe|martinique|guyane|reunion|mayotte seul|"
    r"ile-de-france|bretagne|normandie|occitanie|corse|hauts-de-france|grand est|"
    r"auvergne|bourgogne|centre-val|nouvelle-aquitaine|pays de la loire|provence|zone d'emploi|"
    r"dom|outre-mer|hors france)\b"
)


class Catalog:
    def __init__(self, entries: list[CatalogEntry], key_series: list[dict[str, Any]] | None = None) -> None:
        by_id: dict[str, CatalogEntry] = {}
        for e in entries:
            by_id.setdefault(e.id, e)
        for k in key_series or []:
            e = by_id.get(k["idbank"])
            if e is None:
                e = CatalogEntry(id=k["idbank"], t=k.get("title", ""), f=k.get("freq", ""), df=k.get("dataflow", ""))
                by_id[e.id] = e
            e.key = k["description"] + " " + " ".join(k.get("keywords", []))
            e.boost = float(k.get("boost", 1.0))
        self.entries = list(by_id.values())
        self.by_id = by_id
        docs = [tokenize(f"{e.t} {e.key} {e.key}") for e in self.entries]
        self.bm25 = BM25Okapi(docs) if docs else None
        self.max_last = max((_period_key(e.last) for e in self.entries if e.last), default=date.min)

    def prior(self, e: CatalogEntry, as_of: date | None) -> float:
        """Multiplicative prior: headline national, current, seasonally adjusted series first."""
        t = fold(e.t)
        p = 1.0
        if e.key:
            p *= 1.5 * e.boost
        if e.corr in ("CVS", "CVS-CJO") or "cvs" in t:
            p *= 1.06
        if " ensemble" in t or t.startswith("ensemble"):
            p *= 1.05
        if e.area in ("FR", "FE", "FR-D976", "") and not _REGIONAL.search(t):
            p *= 1.08
        else:
            p *= 0.8
        last = _period_key(e.last) if e.last else None
        first = _period_key(e.first) if e.first else None
        if as_of is not None:
            if last and last.year < as_of.year - 2:
                p *= 0.6  # stopped well before the debate
            if first and first > as_of:
                p *= 0.3  # no data at the time of the debate
        else:
            if e.stop or "arrete" in t:
                p *= 0.75
            if last and self.max_last != date.min and last.year < self.max_last.year - 2:
                p *= 0.7
        # slight preference for shorter (less specific) titles
        p *= 1.0 / (1.0 + 0.004 * max(0, len(e.t) - 60))
        return p

    def search(self, query: str, limit: int = 10, as_of: date | None = None) -> list[tuple[CatalogEntry, float]]:
        if not self.bm25:
            return []
        q = query.strip()
        if re.fullmatch(r"\d{9}", q) and q in self.by_id:
            return [(self.by_id[q], 1.0)]
        toks = tokenize(expand_query(q))
        if not toks:
            return []
        scores = self.bm25.get_scores(toks)
        qset = set(tokenize(q))
        ranked = []
        for i, s in enumerate(scores):
            if s <= 0:
                continue
            e = self.entries[i]
            # coverage of the user's own words (not the expansions)
            doc = set(tokenize(e.t + " " + e.key))
            cov = len(qset & doc) / len(qset) if qset else 1.0
            ranked.append((e, float(s) * self.prior(e, as_of) * (0.5 + 0.5 * cov)))
        ranked.sort(key=lambda x: -x[1])
        return ranked[:limit]


# --------------------------------------------------------------------------------------
# connector


class InseeConnector:
    name = "insee"

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        catalog_path: Path | None = None,
        key_series_path: Path | None = None,
        ttl: float = DATA_TTL,
        http: CachedHttp | None = None,
    ) -> None:
        self.http = http or CachedHttp(cache_dir or CACHE_DIR, ttl=ttl)
        self.catalog_path = catalog_path or CATALOG_PATH
        self.key_series_path = key_series_path or KEY_SERIES_PATH
        self._catalog: Catalog | None = None
        self.key_series = load_key_series(self.key_series_path)

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog(load_catalog(self.catalog_path), self.key_series)
        return self._catalog

    # ---------------------------------------------------------------- tool specs

    def tools(self) -> list[ToolSpec]:
        keys = "\n".join(f"- {k['idbank']}: {k['description']}" for k in self.key_series)
        return [
            ToolSpec(
                name="insee_search_series",
                description=(
                    "Recherche une série statistique officielle de l'INSEE (Banque de données "
                    "macro-économiques) par mots-clés en français : chômage, inflation/prix, dette et "
                    "déficit publics, PIB/croissance, pouvoir d'achat, emploi, salaires, SMIC, commerce "
                    "extérieur, défaillances d'entreprises, naissances, population, logements, budget de "
                    "l'État... Renvoie des identifiants (idbank) à passer ensuite à insee_get_series. "
                    "Inutile si l'indicateur figure dans la liste des séries clés ci-dessous : appeler "
                    "directement insee_get_series avec l'idbank.\nSéries clés (idbank: description):\n"
                    + keys
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Mots-clés en français, ex. 'taux de chômage BIT', "
                            "'prix gazole', 'naissances France', 'dette Maastricht en % du PIB'.",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="insee_get_series",
                description=(
                    "Récupère les valeurs d'une série INSEE (idbank à 9 chiffres) : lignes "
                    "'période: valeur' avec variations calculées (vs période précédente, sur un an, "
                    "moyennes annuelles). Seules les périodes terminées à la date du débat sont "
                    "renvoyées. Les valeurs sont les dernières données révisées (pas les chiffres "
                    "publiés à l'époque). Utiliser start/end pour cibler une période (ex. start='2017', "
                    "end='2022-Q1') ou last_n pour les N dernières observations."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "idbank": {"type": "string", "description": "Identifiant INSEE à 9 chiffres, ex. '001688527'."},
                        "start": {"type": "string", "description": "Début: '2017', '2017-05', '2017-Q2'."},
                        "end": {"type": "string", "description": "Fin: '2022', '2022-03', '2022-Q1'."},
                        "last_n": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                            "description": "Nombre d'observations les plus récentes (défaut selon la fréquence).",
                        },
                    },
                    "required": ["idbank"],
                },
            ),
        ]

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        try:
            if tool == "insee_search_series":
                return self.search(str(args.get("query", "")), int(args.get("limit") or 8), ctx)
            if tool == "insee_get_series":
                return await self.get_series(
                    str(args.get("idbank", "")),
                    start=args.get("start"),
                    end=args.get("end"),
                    last_n=args.get("last_n"),
                    ctx=ctx,
                )
            return ToolResult(content=f"Outil inconnu pour le connecteur INSEE: {tool}")
        except Exception as e:  # never raise into the verifier loop
            log.exception("insee tool %s failed", tool)
            return ToolResult(content=f"Erreur INSEE ({type(e).__name__}): {e}")

    # ---------------------------------------------------------------- search

    def search(self, query: str, limit: int, ctx: VerifyContext) -> ToolResult:
        if not query.strip():
            return ToolResult(content="Requête vide.")
        limit = max(1, min(limit, 25))
        hits = self.catalog.search(query, limit=limit, as_of=ctx.as_of)
        if not hits:
            return ToolResult(content=f"Aucune série INSEE trouvée pour « {query} ». Reformuler avec d'autres mots-clés.")
        lines = [f"Séries INSEE pour « {query} » (idbank | titre | fréquence | période couverte | dataflow):"]
        for e, _score in hits:
            span = f"{e.first or '?'}→{e.last or '?'}"
            star = "★ " if e.key else ""
            lines.append(f"- {e.id} | {star}{e.t} | {FREQ_FR.get(e.f, e.f)} | {span} | {e.df}")
        lines.append("Appeler insee_get_series avec l'idbank choisi.")
        return ToolResult(content=_clip("\n".join(lines)))

    # ---------------------------------------------------------------- get series

    async def fetch_series(self, idbank: str) -> Series | None:
        status, body = await self.http.get(f"{API}/data/SERIES_BDM/{idbank}")
        if status == 404:
            return None
        if status != 200:
            code, texts = parse_error(body)
            raise RuntimeError(f"API INSEE HTTP {status} {code or ''} {' '.join(texts)[:200]}")
        series = parse_sdmx(body)
        return series[0] if series else None

    async def get_series(
        self,
        idbank: str,
        *,
        start: str | None = None,
        end: str | None = None,
        last_n: int | None = None,
        ctx: VerifyContext,
    ) -> ToolResult:
        m = re.search(r"\d{1,10}", idbank or "")
        idbank = m.group(0) if m else ""
        if not idbank:
            return ToolResult(content="idbank manquant (identifiant INSEE à 9 chiffres).")
        idbank = idbank.zfill(9)
        s = await self.fetch_series(idbank)
        if s is None or not s.obs:
            return ToolResult(content=f"Série INSEE {idbank} introuvable. Utiliser insee_search_series.")

        as_of = ctx.as_of
        all_obs = s.obs
        entry = self.catalog.by_id.get(idbank)
        pub_lag = publication_lag(s.freq, entry.df if entry else "")
        unpublished: list[str] = []  # finished at as_of but probably not yet published
        n_future = 0
        if as_of is not None:
            kept = []
            for p, v in all_obs:
                done = obs_end(p, s.title)
                if done + timedelta(days=pub_lag) <= as_of:
                    kept.append((p, v))
                elif done <= as_of:
                    unpublished.append(p)
                else:
                    n_future += 1
            all_obs = kept
        if not all_obs:
            return ToolResult(
                content=f"Série INSEE {idbank} ({s.title}): aucune observation publiée au {as_of} "
                f"(première période: {s.obs[0][0]}, délai de publication estimé {pub_lag} j)."
            )
        start_d, end_d = parse_bound(start, end=False), parse_bound(end, end=True)
        sel = [
            (i, p, v)
            for i, (p, v) in enumerate(all_obs)
            if (start_d is None or period_bounds(p)[0] >= start_d) and (end_d is None or period_bounds(p)[1] <= end_d)
        ]
        lag = FREQ_LAG.get(s.freq, 1)
        default_n = {"M": 13, "T": 9, "A": 12, "S": 8, "B": 12}.get(s.freq, 12)
        if last_n:
            sel = sel[-max(1, min(int(last_n), 120)) :]
        elif start_d is None:
            sel = sel[-default_n:]
        if not sel:
            return ToolResult(
                content=f"Série INSEE {idbank}: aucune observation entre {start or 'début'} et {end or 'fin'} "
                f"(données disponibles: {all_obs[0][0]} → {all_obs[-1][0]})."
            )

        kind = series_kind(s)
        dec = s.decimals
        unit = s.unit
        header = [
            f"INSEE série {idbank} — {s.title}",
            f"Fréquence: {FREQ_FR.get(s.freq, s.freq)} | Unité: {unit or 'n.d.'} | "
            f"Données: {s.obs[0][0]} → {all_obs[-1][0]}"
            + ("" if as_of is not None else f" | MAJ INSEE: {s.attrs.get('LAST_UPDATE', '?')}"),
        ]
        if as_of is not None:
            excl = f"Date du débat {as_of}: seules les observations déjà publiées sont montrées (délai de publication estimé {pub_lag} j après la fin de la période)."
            if unpublished:
                excl += f" Exclues car pas encore publiées à la date du débat: {', '.join(unpublished[-4:])}."
            if n_future:
                excl += f" {n_future} période(s) postérieure(s) exclue(s)."
            header.append(excl)
            header.append(
                "Valeurs = dernières données révisées (INSEE ne publie pas les millésimes; "
                "le chiffre connu à l'époque pouvait différer légèrement)."
            )
        else:
            header.append("Valeurs = dernières données révisées.")

        if kind == "index":
            col = "var. vs période préc. | glissement sur 1 an (%)"
        elif kind == "rate":
            col = "écart vs période préc. | écart sur 1 an (points)"
        else:
            col = "var. vs période préc. | var. sur 1 an"
        lines = [f"Observations (période: valeur | {col}):"] if lag > 1 else [f"Observations (période: valeur | {col.split(' | ')[0]}):"]

        by_period = dict(all_obs)
        for i, p, v in sel:
            prev = all_obs[i - 1][1] if i >= 1 else None
            ya = year_ago(p)
            yago = by_period.get(ya) if ya and lag > 1 else None
            parts = [f"{p}: {fmt_num(v, dec)}"]
            parts.append(_change(kind, v, prev, dec))
            if lag > 1:
                parts.append(_change(kind, v, yago, dec))
            lines.append(" | ".join(parts))

        extra: list[str] = []
        is_change = bool(re.search(r"glissement|variation|evolution|contribution|acquis", fold(s.title)))
        if lag > 1 and not is_change:
            flow = kind == "level" and is_flow(s.title)
            means = _annual_means(all_obs, s.freq, total=flow)
            y_lo, y_hi = _period_key(sel[0][1]).year - 3, _period_key(sel[-1][1]).year
            years = [y for y in sorted(means) if y_lo <= y <= y_hi][-4:]
            if years:
                txt = []
                for y in years:
                    m = means[y]
                    item = f"{y}: {fmt_num(m, dec if dec is not None else 2)}"
                    prev_m = means.get(y - 1)
                    if kind == "index" and prev_m:
                        item += f" ({(m / prev_m - 1) * 100:+.1f} % en moyenne annuelle)"
                    elif flow and prev_m and prev_m > 0 and m > 0:
                        item += f" ({(m / prev_m - 1) * 100:+.1f} %)"
                    txt.append(item)
                label = "Totaux annuels (somme des périodes)" if flow else "Moyennes annuelles"
                extra.append(f"{label}: " + " ; ".join(txt))
        if len(sel) >= 2 and sel[0][2] is not None and sel[-1][2] is not None:
            a, b = sel[0][2], sel[-1][2]
            if kind == "rate":
                extra.append(f"Écart {sel[0][1]} → {sel[-1][1]}: {b - a:+.{max(dec or 1, 1)}f} points")
            elif a > 0 and b > 0:
                extra.append(f"Évolution {sel[0][1]} → {sel[-1][1]}: {fmt_num(b - a, dec)} ({(b / a - 1) * 100:+.1f} %)")

        last_p, last_v = sel[-1][1], sel[-1][2]
        extra.append(f"Dernière valeur retenue: {last_p} = {fmt_num(last_v, dec)} {unit}".rstrip())

        content = _clip("\n".join(header + lines + extra), keep_tail=len("\n".join(extra)) + 10)
        excerpt_bits = [f"{p}: {fmt_num(v, dec)}" for _, p, v in sel[-3:]]
        source = Source(
            kind="official_data",
            publisher="INSEE",
            title=s.title,
            url=f"https://www.insee.fr/fr/statistiques/serie/{idbank}",
            date=last_p,
            excerpt=(" ; ".join(excerpt_bits) + (f" ({unit})" if unit else ""))[:300],
        )
        return ToolResult(content=content, sources=[source])


def _change(kind: str, v: float | None, ref: float | None, dec: int | None = None) -> str:
    if v is None or ref is None:
        return "n.d."
    if kind == "rate":
        return f"{v - ref:+.{max(dec if dec is not None else 2, 1)}f} pt"
    if ref == 0:
        return f"{fmt_num(v - ref)}"
    pct = (v / ref - 1) * 100
    if kind == "index":
        return f"{pct:+.1f} %"
    diff = f"{v - ref:+,.{_auto_dec(v - ref)}f}".replace(",", " ")
    return f"{diff} ({pct:+.1f} %)" if ref > 0 and v >= 0 else diff


def _auto_dec(x: float) -> int:
    ax = abs(x)
    return 0 if ax >= 100 or float(x).is_integer() else 1 if ax >= 10 else 2


def _clip(text: str, limit: int = MAX_CONTENT, keep_tail: int = 0) -> str:
    """Trim the middle of long outputs (keeps header and the tail)."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    head, tail = lines[:4], []
    budget = limit - sum(len(x) + 1 for x in head) - 40
    # keep as many trailing lines as fit
    for line in reversed(lines[4:]):
        if budget - len(line) - 1 < 0:
            break
        tail.insert(0, line)
        budget -= len(line) + 1
    return "\n".join(head + ["[… observations plus anciennes omises …]"] + tail)


# --------------------------------------------------------------------------------------
# catalog building (CLI)


async def _harvest_flow(http: CachedHttp, flow: str, key: str = "all", depth: int = 0) -> list[Series]:
    """Series metadata + last observation of a dataflow; follows INSEE's split hints on 413."""
    url = f"{API}/data/{flow}/{key}"
    status, body = await http.get(url, {"lastNObservations": 1})
    if status == 200:
        return parse_sdmx(body)
    code, texts = parse_error(body)
    if status == 413 or code == "510":
        subs = [t.split(f"/data/{flow}/", 1)[1] for t in texts if t.startswith(f"/data/{flow}/")]
        if depth >= 3 or not subs:
            log.warning("%s/%s too large and no split hint (%s)", flow, key, texts)
            return []
        out: list[Series] = []
        for sub in subs:
            out += await _harvest_flow(http, flow, sub, depth + 1)
        return out
    if status == 404:
        return []
    log.warning("%s/%s: HTTP %s %s", flow, key, status, texts)
    return []


async def _first_periods(http: CachedHttp, flow: str, key: str = "all", depth: int = 0) -> dict[str, str]:
    url = f"{API}/data/{flow}/{key}"
    status, body = await http.get(url, {"firstNObservations": 1})
    if status == 200:
        return {s.idbank: s.obs[0][0] for s in parse_sdmx(body) if s.obs}
    code, texts = parse_error(body)
    if (status == 413 or code == "510") and depth < 3:
        out: dict[str, str] = {}
        for t in texts:
            if t.startswith(f"/data/{flow}/"):
                out.update(await _first_periods(http, flow, t.split(f"/data/{flow}/", 1)[1], depth + 1))
        return out
    return {}


async def build_catalog(
    flows: list[str] | None = None, out_path: Path = CATALOG_PATH, cache_dir: Path = CACHE_DIR / "build"
) -> int:
    http = CachedHttp(cache_dir, ttl=BUILD_TTL, min_interval=0.8, timeout=120)
    flows = flows or CATALOG_DATAFLOWS
    entries: dict[str, CatalogEntry] = {}
    for flow in flows:
        t0 = time.time()
        series = await _harvest_flow(http, flow)
        firsts = await _first_periods(http, flow) if series else {}
        for s in series:
            if not s.idbank or s.idbank in entries:
                continue
            a = s.attrs
            entries[s.idbank] = CatalogEntry(
                id=s.idbank,
                t=s.title,
                f=s.freq,
                u=a.get("UNIT_MEASURE", ""),
                m=a.get("UNIT_MULT", "0"),
                df=flow,
                first=firsts.get(s.idbank, ""),
                last=s.obs[-1][0] if s.obs else "",
                lu=a.get("LAST_UPDATE", ""),
                area=a.get("REF_AREA", ""),
                corr=a.get("CORRECTION", ""),
                stop=a.get("SERIE_ARRETEE", "").upper() == "TRUE" or "arrêtée" in s.title.lower(),
                nat=a.get("NATURE", ""),
            )
        print(f"{flow:45s} {len(series):6d} series  ({time.time() - t0:.1f}s)", file=sys.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        for e in entries.values():
            d = {k: v for k, v in e.__dict__.items() if v not in ("", False, "0") and k not in ("key", "boost")}
            f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(out_path)
    print(f"wrote {len(entries)} series to {out_path} ({out_path.stat().st_size / 1e6:.2f} MB)", file=sys.stderr)
    return len(entries)


async def verify_key_series(path: Path = KEY_SERIES_PATH, *, update: bool = False) -> bool:
    """Check every curated idbank exists and its title matches the stored one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    http = CachedHttp(CACHE_DIR / "build", ttl=0, min_interval=0.8)
    ok = True
    ids = [k["idbank"] for k in data["series"]]
    found: dict[str, Series] = {}
    for i in range(0, len(ids), 20):
        status, body = await http.get(f"{API}/data/SERIES_BDM/{'+'.join(ids[i : i + 20])}", {"lastNObservations": 1})
        if status == 200:
            for s in parse_sdmx(body):
                found[s.idbank] = s
    for k in data["series"]:
        s = found.get(k["idbank"])
        if s is None:
            print(f"MISSING  {k['idbank']}  {k['description']}")
            ok = False
            continue
        if update:
            k["title"], k["freq"] = s.title, s.freq
            k["last"] = s.obs[-1][0] if s.obs else ""
        if k.get("title") != s.title:
            print(f"MISMATCH {k['idbank']}: stored={k.get('title')!r} api={s.title!r}")
            ok = False
        else:
            print(f"ok       {k['idbank']}  {s.obs[-1][0] if s.obs else '-':8s} {s.title[:110]}")
    if update:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ok


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m sic.sources.insee")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-catalog", help="harvest dataflows into data/sources/insee/catalog.jsonl.gz")
    b.add_argument("--flows", nargs="*", help="subset of dataflows (default: curated list)")
    v = sub.add_parser("verify-keys", help="check curated key series against the API")
    v.add_argument("--update", action="store_true", help="refresh stored titles/freq from the API")
    s = sub.add_parser("search", help="search the catalog")
    s.add_argument("query")
    s.add_argument("--as-of")
    g = sub.add_parser("get", help="fetch a series")
    g.add_argument("idbank")
    g.add_argument("--as-of")
    g.add_argument("--start")
    g.add_argument("--end")
    g.add_argument("--last-n", type=int)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.cmd == "build-catalog":
        asyncio.run(build_catalog(args.flows))
        return 0
    if args.cmd == "verify-keys":
        return 0 if asyncio.run(verify_key_series(update=args.update)) else 1
    conn = InseeConnector()
    ctx = VerifyContext(as_of=date.fromisoformat(args.as_of) if args.as_of else None)
    if args.cmd == "search":
        res = asyncio.run(conn.call("insee_search_series", {"query": args.query, "limit": 10}, ctx))
    else:
        res = asyncio.run(
            conn.call(
                "insee_get_series",
                {"idbank": args.idbank, "start": args.start, "end": args.end, "last_n": args.last_n},
                ctx,
            )
        )
    print(res.content)
    for src in res.sources:
        print("SOURCE:", src.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
