"""Assemblée nationale connector: public votes (scrutins publics) of deputies.

Backed by a local SQLite index built from the open data of
https://data.assemblee-nationale.fr (no auth):

* scrutins of legislatures 14 (2012-2017, open data covers n° 1-1354 only, up to
  2016-11-24), 15 (2017-2022), 16 (2022-2024) and 17 (2024-...);
* the "AMO30" file: every actor / mandate / organ since the XIth legislature.

Build (downloads into ``config.CACHE/"assemblee"``):

    uv run python -m sic.sources.assemblee build [--force] [--refresh] [--offline]

Debug queries (same output as the LLM tools):

    uv run python -m sic.sources.assemblee deputy "Le Pen"
    uv run python -m sic.sources.assemblee votes "Le Pen" "crise sanitaire" [--as-of 2022-04-20]
    uv run python -m sic.sources.assemblee scrutins "climat résilience"
    uv run python -m sic.sources.assemblee stats "Le Pen" [--from 2017] [--to 2022]
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path
from typing import Any

from sic import config
from sic.models import Source
from sic.sources.base import ToolResult, ToolSpec, VerifyContext

SCHEMA_VERSION = 3
CACHE_DIR = config.CACHE / "assemblee"
DB_PATH = CACHE_DIR / "an.sqlite"

_BASE = "https://data.assemblee-nationale.fr/static/openData/repository"
SCRUTIN_URLS: dict[int, str] = {
    14: f"{_BASE}/14/loi/scrutins/Scrutins_XIV.json.zip",
    15: f"{_BASE}/15/loi/scrutins/Scrutins_XV.json.zip",
    16: f"{_BASE}/16/loi/scrutins/Scrutins.json.zip",
    17: f"{_BASE}/17/loi/scrutins/Scrutins.json.zip",
}
AMO_URL = f"{_BASE}/17/amo/tous_acteurs_mandats_organes_xi_legislature/AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json.zip"
FROZEN_LEGISLATURES = {
    14,
    15,
    16,
}  # archives, never change: no need to re-check upstream
CURRENT_LEGISLATURE = 17

PUBLISHER = "Assemblée nationale"
MAX_CONTENT = 3900

POS_LABEL = {
    "pour": "POUR",
    "contre": "CONTRE",
    "abstention": "ABSTENTION",
    "non_votant": "NON-VOTANT (présent, n'a pas pris part au vote)",
}
CAUSE_LABEL = {
    "PAN": "président de l'Assemblée",
    "PSE": "président de séance",
    "MG": "membre du gouvernement",
}

KIND_LABEL = {
    "ensemble": "VOTE SUR L'ENSEMBLE DU TEXTE",
    "censure": "MOTION DE CENSURE",
    "declaration": "DÉCLARATION DU GOUVERNEMENT",
    "resolution": "PROPOSITION DE RÉSOLUTION",
    "motion": "motion de procédure (rejet/renvoi)",
    "partie": "partie/crédits d'un projet de loi de finances",
    "article": "article",
    "amendement": "amendement",
    "autre": "autre",
}
KIND_BUCKET = {
    "ensemble": 0,
    "censure": 0,
    "declaration": 0,
    "resolution": 0,
    "motion": 1,
    "partie": 1,
    "article": 2,
    "amendement": 3,
    "autre": 4,
}
WHOLE_TEXT_KINDS = ("ensemble", "censure", "declaration", "resolution")

# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------


def norm(s: str | None) -> str:
    """Lowercase, strip accents, unify apostrophes/dashes, collapse whitespace."""
    if not s:
        return ""
    s = s.replace("’", "'").replace("ʼ", "'").replace("‘", "'").replace("œ", "oe").replace("Œ", "oe")
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def norm_name(s: str | None) -> str:
    s = norm(s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify(titre: str, type_code: str | None = None) -> str:
    """Kind of a scrutin from its title. "ensemble" = vote on the whole text."""
    t = norm(titre).lstrip('« "')
    if t.startswith(
        (
            "l'ensemble",
            "l ensemble",
            "le projet de loi",
            "la proposition de loi",
            "le texte",
        )
    ):
        return "ensemble"
    if t.startswith("la motion de censure") or "motion de censure" in t[:40]:
        return "censure"
    if t.startswith(("la declaration du gouvernement", "la declaration de politique generale")):
        return "declaration"
    if t.startswith(("la proposition de resolution", "le projet de resolution")):
        return "resolution"
    if t.startswith(("la motion", "les motions")):
        return "motion"
    if t.startswith(
        (
            "la premiere partie",
            "la seconde partie",
            "la deuxieme partie",
            "les credits",
            "l'etat ",
        )
    ):
        return "partie"
    if t.startswith(("l'article", "les articles", "l article")):
        return "article"
    if t.startswith(
        (
            "l'amendement",
            "les amendements",
            "le sous-amendement",
            "le sous amendement",
            "les sous-amendements",
            "l amendement",
        )
    ):
        return "amendement"
    if type_code == "MOC":
        return "censure"
    return "autre"


def short_title(titre: str, n: int = 110) -> str:
    t = re.sub(r"\s+", " ", (titre or "").strip()).rstrip(".")
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def _parse_date(s: str | None, end: bool = False) -> str | None:
    """Accept YYYY, YYYY-MM, YYYY-MM-DD (also DD/MM/YYYY). Returns ISO date string."""
    if not s:
        return None
    s = str(s).strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        s = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-12-31" if end else f"{s}-01-01"
    if re.fullmatch(r"\d{4}-\d{1,2}", s):
        y, mo = s.split("-")
        if end:
            import calendar

            return f"{y}-{int(mo):02d}-{calendar.monthrange(int(y), int(mo))[1]:02d}"
        return f"{y}-{int(mo):02d}-01"
    try:
        return Date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        raise ValueError(f"date invalide: {s!r} (format attendu AAAA-MM-JJ)") from None


# ---------------------------------------------------------------------------
# raw JSON helpers
# ---------------------------------------------------------------------------


def _L(x: Any) -> list[Any]:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _txt(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, dict):
        if x.get("@xsi:nil") == "true":
            return None
        return x.get("#text")
    s = str(x).strip()
    return s or None


def _int(x: Any) -> int | None:
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def _iter_json_docs(path: Path) -> Iterator[Any]:
    """Yield parsed JSON documents from a .zip (all .json members), a directory, or a .json file."""
    path = Path(path)
    if path.is_dir():
        for p in sorted(path.rglob("*.json")):
            yield json.loads(p.read_bytes())
    elif path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(".json"):
                    yield json.loads(z.read(name))
    else:
        yield json.loads(path.read_bytes())


def iter_scrutins(path: Path) -> Iterator[dict[str, Any]]:
    for doc in _iter_json_docs(path):
        if "scrutin" in doc:
            yield from _L(doc["scrutin"])
        elif "scrutins" in doc:
            yield from _L(doc["scrutins"].get("scrutin"))


def iter_amo(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for doc in _iter_json_docs(path):
        if "acteur" in doc:
            yield "acteur", doc["acteur"]
        elif "organe" in doc:
            yield "organe", doc["organe"]
        elif "export" in doc:  # single-file export variant
            ex = doc["export"]
            for o in _L((ex.get("organes") or {}).get("organe")):
                yield "organe", o
            for a in _L((ex.get("acteurs") or {}).get("acteur")):
                yield "acteur", a


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

_NOMINATIF_KEYS = {
    "pours": "pour",
    "pour": "pour",
    "contres": "contre",
    "contre": "contre",
    "abstentions": "abstention",
    "abstention": "abstention",
    "nonVotants": "non_votant",
    "nonVotant": "non_votant",
    "nonVotantsVolontaires": "non_votant",
}
_POS_ORDER = {"pour": 0, "contre": 1, "abstention": 2, "non_votant": 3}


def scrutin_id(legislature: int, numero: int) -> int:
    return legislature * 100000 + numero


def _votants(block: Any) -> Iterator[dict[str, Any]]:
    """Yield votant dicts from a decompte block (dict with 'votant', list of those, or junk)."""
    for b in _L(block):
        if isinstance(b, dict):
            for v in _L(b.get("votant")):
                if isinstance(v, dict) and v.get("acteurRef"):
                    yield v


@dataclass
class ParsedScrutin:
    row: tuple[Any, ...]
    fts: str
    groups: list[tuple[Any, ...]] = field(default_factory=list)
    positions: list[tuple[Any, ...]] = field(default_factory=list)
    corrections: list[tuple[Any, ...]] = field(default_factory=list)


def parse_scrutin(s: dict[str, Any]) -> ParsedScrutin:
    leg = int(s["legislature"])
    num = int(s["numero"])
    sid = scrutin_id(leg, num)
    titre = re.sub(r"\s+", " ", (s.get("titre") or "").strip())
    objet = re.sub(r"\s+", " ", ((s.get("objet") or {}).get("libelle") or "").strip())
    tv = s.get("typeVote") or {}
    type_code = tv.get("codeTypeVote")
    sort = ((s.get("sort") or {}).get("code") or "").strip()
    syn = s.get("syntheseVote") or {}
    dec = syn.get("decompte") or {}
    mode = s.get("modePublicationDesVotes") or "DecompteNominatif"
    nominatif = 1 if mode == "DecompteNominatif" else 0
    demandeur = (s.get("demandeur") or {}).get("texte")

    groups: list[tuple[Any, ...]] = []
    positions: dict[str, tuple[Any, ...]] = {}
    for organe in _L((s.get("ventilationVotes") or {}).get("organe")):
        for g in _L(((organe or {}).get("groupes") or {}).get("groupe")):
            gref = g.get("organeRef")
            vote = g.get("vote") or {}
            dv = vote.get("decompteVoix") or {}
            groups.append(
                (
                    sid,
                    gref,
                    _int(g.get("nombreMembresGroupe")),
                    vote.get("positionMajoritaire"),
                    _int(dv.get("pour")),
                    _int(dv.get("contre")),
                    _int(dv.get("abstentions", dv.get("abstention"))),
                    _int(dv.get("nonVotants", dv.get("nonVotant"))),
                )
            )
            dn = vote.get("decompteNominatif") or {}
            if not isinstance(dn, dict):
                continue
            for key, block in dn.items():
                pos = _NOMINATIF_KEYS.get(key)
                if not pos:
                    continue
                for v in _votants(block):
                    a = v["acteurRef"]
                    prev = positions.get(a)
                    if prev is not None and _POS_ORDER[prev[2]] <= _POS_ORDER[pos]:
                        continue
                    positions[a] = (
                        a,
                        sid,
                        pos,
                        1 if str(v.get("parDelegation")).lower() == "true" else 0,
                        gref,
                        v.get("causePositionVote"),
                    )

    corrections: list[tuple[Any, ...]] = []
    seen_corr: set[tuple[str, str, int]] = set()
    map_ = s.get("miseAuPoint") or {}
    if isinstance(map_, dict):
        for key, block in map_.items():
            if key == "dysfonctionnement":
                sub_items = list(block.items()) if isinstance(block, dict) else []
                dysf = 1
            else:
                sub_items = [(key, block)]
                dysf = 0
            for k2, b2 in sub_items:
                pos = _NOMINATIF_KEYS.get(k2)
                if not pos:
                    continue
                for v in _votants(b2):
                    tup = (v["acteurRef"], pos, dysf)
                    if tup in seen_corr:
                        continue
                    seen_corr.add(tup)
                    corrections.append((sid, v["acteurRef"], pos, dysf))

    n_votes = sum(1 for p in positions.values() if p[2] != "non_votant")
    row = (
        sid,
        leg,
        num,
        s.get("dateScrutin"),
        titre,
        objet if objet and objet != titre else None,
        sort,
        type_code,
        tv.get("libelleTypeVote"),
        classify(titre, type_code),
        demandeur,
        nominatif,
        _int(syn.get("nombreVotants")),
        _int(syn.get("suffragesExprimes")),
        _int(syn.get("nbrSuffragesRequis")),
        _int(dec.get("pour")),
        _int(dec.get("contre")),
        _int(dec.get("abstentions", dec.get("abstention"))),
        _int(dec.get("nonVotants", dec.get("nonVotant"))),
        n_votes,
    )
    fts = norm(titre + (" " + objet if objet and objet != titre else "") + " " + CENSURE_CONTEXT.get((leg, num), ""))
    return ParsedScrutin(
        row=row,
        fts=fts,
        groups=groups,
        positions=list(positions.values()),
        corrections=corrections,
    )


def parse_acteur(
    a: dict[str, Any],
) -> tuple[tuple[Any, ...], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    uid = _txt(a.get("uid")) or ""
    ident = ((a.get("etatCivil") or {}).get("ident")) or {}
    prenom = _txt(ident.get("prenom")) or ""
    nom = _txt(ident.get("nom")) or ""
    naiss = _txt((((a.get("etatCivil") or {}).get("infoNaissance")) or {}).get("dateNais"))
    mandats: list[tuple[Any, ...]] = []
    gps: list[tuple[Any, ...]] = []
    for m in _L((a.get("mandats") or {}).get("mandat")):
        t = m.get("typeOrgane")
        organe = _txt((m.get("organes") or {}).get("organeRef"))
        if t == "ASSEMBLEE":
            lieu = ((m.get("election") or {}).get("lieu")) or {}
            # dateDebut is the election date of the seat; a substitute (or a minister coming back) only
            # sits from datePriseFonction, which is the right start for "in office" checks.
            debut = _txt(m.get("dateDebut"))
            prise = _txt((m.get("mandature") or {}).get("datePriseFonction"))
            if prise and (not debut or prise > debut):
                debut = prise
            mandats.append(
                (
                    uid,
                    _int(m.get("legislature")),
                    debut,
                    _txt(m.get("dateFin")),
                    _txt(lieu.get("departement")),
                    _txt(lieu.get("numDepartement")),
                    _txt(lieu.get("numCirco")),
                    _txt((m.get("mandature") or {}).get("causeFin")),
                )
            )
        elif t == "GP":
            q = ((m.get("infosQualite") or {}).get("codeQualite")) or ""
            gps.append(
                (
                    uid,
                    organe,
                    _int(m.get("legislature")),
                    _txt(m.get("dateDebut")),
                    _txt(m.get("dateFin")),
                    q,
                )
            )
    row = (
        uid,
        _txt(ident.get("civ")),
        prenom,
        nom,
        norm_name(f"{prenom} {nom}"),
        norm_name(nom),
        naiss,
        1 if mandats else 0,
    )
    return row, mandats, gps


def parse_organe(o: dict[str, Any]) -> tuple[Any, ...] | None:
    code = o.get("codeType")
    if code not in ("GP", "ASSEMBLEE", "PARPOL"):
        return None
    vi = o.get("viMoDe") or {}
    return (
        _txt(o.get("uid")),
        code,
        _txt(o.get("libelle")),
        _txt(o.get("libelleAbrev")),
        _txt(o.get("libelleAbrege")),
        _int(o.get("legislature")),
        _txt(vi.get("dateDebut")),
        _txt(vi.get("dateFin")),
    )


# ---------------------------------------------------------------------------
# index build
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE organes (uid TEXT PRIMARY KEY, code_type TEXT, libelle TEXT, abrev TEXT, abrege TEXT,
    legislature INTEGER, date_debut TEXT, date_fin TEXT);
CREATE TABLE acteurs (uid TEXT PRIMARY KEY, civ TEXT, prenom TEXT, nom TEXT, norm_full TEXT, norm_nom TEXT,
    date_naissance TEXT, is_depute INTEGER);
CREATE TABLE mandats (acteur TEXT, legislature INTEGER, date_debut TEXT, date_fin TEXT, departement TEXT,
    num_departement TEXT, circo TEXT, cause_fin TEXT);
CREATE TABLE gp_mandats (acteur TEXT, organe TEXT, legislature INTEGER, date_debut TEXT, date_fin TEXT, qualite TEXT);
CREATE TABLE scrutins (id INTEGER PRIMARY KEY, legislature INTEGER, numero INTEGER, date TEXT, titre TEXT, objet TEXT,
    sort TEXT, type_code TEXT, type_libelle TEXT, kind TEXT, demandeur TEXT, nominatif INTEGER,
    votants INTEGER, exprimes INTEGER, requis INTEGER, pour INTEGER, contre INTEGER, abstentions INTEGER, non_votants INTEGER,
    n_votes INTEGER, n_exercice INTEGER);
CREATE TABLE group_votes (scrutin INTEGER, organe TEXT, nb_membres INTEGER, position TEXT,
    pour INTEGER, contre INTEGER, abstentions INTEGER, non_votants INTEGER, PRIMARY KEY (scrutin, organe)) WITHOUT ROWID;
CREATE TABLE positions (acteur TEXT, scrutin INTEGER, position TEXT, delegation INTEGER, groupe TEXT, cause TEXT,
    PRIMARY KEY (acteur, scrutin)) WITHOUT ROWID;
CREATE TABLE corrections (scrutin INTEGER, acteur TEXT, position TEXT, dysfonctionnement INTEGER);
CREATE VIRTUAL TABLE scrutins_fts USING fts5(texte, content='', tokenize="unicode61 remove_diacritics 2");
"""

POST_INDEXES = """
CREATE INDEX scrutins_date ON scrutins(date);
CREATE INDEX mandats_acteur ON mandats(acteur);
CREATE INDEX mandats_leg ON mandats(legislature, date_debut);
CREATE INDEX gp_acteur ON gp_mandats(acteur);
CREATE INDEX corrections_acteur ON corrections(acteur, scrutin);
"""


def build_index(
    db_path: Path,
    scrutin_paths: Iterable[Path],
    amo_path: Path,
    fingerprint: str = "",
    log: Any = None,
) -> dict[str, Any]:
    """Build the SQLite index from local files (zips, directories or JSON files). Atomic replace."""
    say = log or (lambda *_: None)
    t0 = time.time()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".sqlite.tmp")
    for p in (tmp, Path(str(tmp) + "-journal")):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA cache_size=-200000")
    con.executescript(SCHEMA)

    n_act = n_org = 0
    acts, mands, gps, orgs = [], [], [], []
    for kind, d in iter_amo(Path(amo_path)):
        if kind == "acteur":
            row, m, g = parse_acteur(d)
            if row[0]:
                acts.append(row)
                mands.extend(m)
                gps.extend(g)
        else:
            o = parse_organe(d)
            if o and o[0]:
                orgs.append(o)
    con.executemany("INSERT OR REPLACE INTO acteurs VALUES (?,?,?,?,?,?,?,?)", acts)
    con.executemany("INSERT INTO mandats VALUES (?,?,?,?,?,?,?,?)", mands)
    con.executemany("INSERT INTO gp_mandats VALUES (?,?,?,?,?,?)", gps)
    con.executemany("INSERT OR REPLACE INTO organes VALUES (?,?,?,?,?,?,?,?)", orgs)
    n_act, n_org = len(acts), len(orgs)
    say(f"acteurs: {n_act}, organes (GP/ASSEMBLEE/PARPOL): {n_org} ({time.time() - t0:.1f}s)")

    n_scr = n_pos = 0
    for path in scrutin_paths:
        t1 = time.time()
        rows, fts, grp, pos, corr = [], [], [], [], []
        for s in iter_scrutins(Path(path)):
            try:
                p = parse_scrutin(s)
            except Exception as e:  # noqa: BLE001 - skip one malformed record, keep the build going
                say(f"  skip scrutin {s.get('uid')}: {e!r}")
                continue
            rows.append(p.row)
            fts.append((p.row[0], p.fts))
            grp.extend(p.groups)
            pos.extend(p.positions)
            corr.extend(p.corrections)
        con.executemany(f"INSERT OR REPLACE INTO scrutins VALUES ({','.join('?' * 20)}, NULL)", rows)
        con.executemany("INSERT INTO scrutins_fts(rowid, texte) VALUES (?,?)", fts)
        con.executemany("INSERT OR REPLACE INTO group_votes VALUES (?,?,?,?,?,?,?,?)", grp)
        con.executemany("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)", pos)
        con.executemany("INSERT INTO corrections VALUES (?,?,?,?)", corr)
        n_scr += len(rows)
        n_pos += len(pos)
        say(f"{Path(path).name}: {len(rows)} scrutins, {len(pos)} positions ({time.time() - t1:.1f}s)")

    con.executescript(POST_INDEXES)
    # number of deputies in office at each scrutin (to compute average participation)
    con.execute(
        """UPDATE scrutins SET n_exercice = (
             SELECT count(DISTINCT m.acteur) FROM mandats m
             WHERE m.legislature = scrutins.legislature AND m.date_debut <= scrutins.date
               AND (m.date_fin IS NULL OR m.date_fin >= scrutins.date))"""
    )
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "fingerprint": fingerprint,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_scrutins": str(n_scr),
        "n_positions": str(n_pos),
        "n_acteurs": str(n_act),
        "max_date": con.execute("SELECT max(date) FROM scrutins").fetchone()[0] or "",
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    con.commit()
    con.execute("INSERT INTO scrutins_fts(scrutins_fts) VALUES ('optimize')")
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    say(f"index: {db_path} ({db_path.stat().st_size / 1e6:.1f} MB) in {time.time() - t0:.1f}s")
    return {**meta, "seconds": round(time.time() - t0, 1)}


def _download(url: str, dest: Path, refresh: bool, log: Any, retries: int = 3) -> Path:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(30.0, read=180.0)
    if dest.exists() and not refresh:
        return dest
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "sic-factcheck/0.1"},
            ) as c:
                if dest.exists():
                    h = c.head(url)
                    h.raise_for_status()
                    if int(h.headers.get("content-length", -1)) == dest.stat().st_size:
                        log(f"up to date: {dest.name}")
                        return dest
                tmp = dest.with_suffix(dest.suffix + ".part")
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        f.writelines(r.iter_bytes(1 << 20))
                with zipfile.ZipFile(tmp) as z:  # validate
                    if z.testzip() is not None:
                        raise zipfile.BadZipFile("corrupted member")
                os.replace(tmp, dest)
                log(f"downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
                return dest
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"download failed ({attempt}/{retries}) {url}: {e!r}")
            time.sleep(2 * attempt)
    if dest.exists():
        log(f"using stale local copy {dest.name}")
        return dest
    raise RuntimeError(f"cannot download {url}: {last!r}")


def build(
    force: bool = False,
    refresh: bool = False,
    offline: bool = False,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Download (if needed) and build the index. Skips the build if sources are unchanged."""

    def log(m: str) -> None:
        print(m, file=sys.stderr, flush=True)

    files: list[Path] = []
    for leg, url in SCRUTIN_URLS.items():
        dest = CACHE_DIR / f"scrutins_{leg}.json.zip"
        if offline:
            if not dest.exists():
                raise RuntimeError(f"missing {dest} (offline)")
        else:
            _download(url, dest, refresh=refresh and leg not in FROZEN_LEGISLATURES, log=log)
        files.append(dest)
    amo = CACHE_DIR / "amo30.json.zip"
    if not offline:
        _download(AMO_URL, amo, refresh=refresh, log=log)
    elif not amo.exists():
        raise RuntimeError(f"missing {amo} (offline)")
    fp = json.dumps([SCHEMA_VERSION] + [(p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in [*files, amo]])
    if db_path.exists() and not force:
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
                old = dict(con.execute("SELECT key, value FROM meta").fetchall())
            if old.get("fingerprint") == fp:
                log(f"index up to date: {db_path}")
                return {**old, "skipped": True}
        except sqlite3.Error:
            pass
    return build_index(db_path, files, amo, fingerprint=fp, log=log)


# ---------------------------------------------------------------------------
# query layer
# ---------------------------------------------------------------------------

_STOPWORDS_TXT = """a au aux avec ce ces cet cette d dans de des du en et l la le les leur leurs mon ma mes ne ni no
non nos notre ou par pas pour qu que qui sa se ses son sur ta te tes ton un une vos votre y n s c j m loi lois projet
proposition texte textes vote votes voter scrutin scrutins depute deputee relatif relative relatifs relatives visant
portant"""
STOPWORDS = frozenset(_STOPWORDS_TXT.split())

# Popular names -> words of the official titles (verified against the index).
ALIASES: dict[str, list[str]] = {
    "passe vaccinal": ["renforcant outils gestion crise sanitaire"],
    "pass vaccinal": ["renforcant outils gestion crise sanitaire"],
    "pass sanitaire": [
        "gestion crise sanitaire",
        "sortie etat urgence sanitaire",
        "vigilance sanitaire",
    ],
    "passe sanitaire": [
        "gestion crise sanitaire",
        "sortie etat urgence sanitaire",
        "vigilance sanitaire",
    ],
    "retraite": [
        "systeme universel retraite",
        "financement rectificative securite sociale 2023",
    ],
    "loi climat": ["dereglement climatique resilience"],
    "climat et resilience": ["dereglement climatique resilience"],
    "mariage pour tous": ["mariage couples personnes meme sexe"],
    "loi el khomri": [
        "travail modernisation dialogue social securisation parcours professionnels",
        "nouvelles libertes nouvelles protections entreprises actifs",
    ],
    "ordonnances travail": ["renforcement dialogue social"],
    "loi immigration": [
        "controler immigration ameliorer integration",
        "immigration maitrisee droit asile effectif",
    ],
    "loi asile immigration": ["immigration maitrisee droit asile effectif"],
    "loi separatisme": ["confortant respect principes republique"],
    "loi anticasseurs": ["prevenir violences manifestations sanctionner auteurs"],
    "loi anti casseurs": ["prevenir violences manifestations sanctionner auteurs"],
    "ivg constitution": ["liberte recourir interruption volontaire grossesse"],
    "fin de vie": ["aide a mourir", "fin de vie"],
    "loi pacte": ["croissance transformation entreprises"],
    "loi elan": ["evolution logement amenagement numerique"],
    "loi egalim": ["equilibre relations commerciales secteur agricole"],
    "loi duplomb": ["contraintes exercice metier agriculteur"],
    "loi macron": ["croissance activite egalite chances economiques"],
    "decheance de nationalite": ["protection nation"],
    "loi kasbarian": ["proteger logements occupation illicite"],
    "secu globale": ["securite globale"],
}


# Motions de censure (art. 49.3) whose official title does not name the text. Context appended to the
# searchable text and shown in results. Keyed by (legislature, numero).
CENSURE_CONTEXT: dict[tuple[int, int], str] = {
    (
        15,
        2706,
    ): "49.3 sur le projet de loi instituant un système universel de retraite (réforme des retraites 2020)",
    (
        15,
        2707,
    ): "49.3 sur le projet de loi instituant un système universel de retraite (réforme des retraites 2020)",
    (
        16,
        1240,
    ): "49.3 sur le projet de loi de financement rectificative de la sécurité sociale pour 2023 (réforme des retraites)",
    (
        16,
        1241,
    ): "49.3 sur le projet de loi de financement rectificative de la sécurité sociale pour 2023 (réforme des retraites)",
    (
        17,
        519,
    ): "49.3 sur le projet de loi de financement de la sécurité sociale pour 2025 (chute du gouvernement Barnier)",
}


def matched_aliases(query: str) -> list[str]:
    nq = " ".join(re.split(r"[^a-z0-9]+", norm(query)))
    return [k for k in ALIASES if k in nq]


def _alias_note(query: str) -> str:
    seen: list[str] = []
    for k in matched_aliases(query):
        for e in ALIASES[k]:
            if e not in seen:
                seen.append(e)
    return " / ".join(seen)


def _fts_term(tok: str) -> str:
    t = tok
    if len(t) > 4 and t[-1] in "sx":
        t = t[:-1]
    return f'"{t}"*' if len(t) >= 4 else f'"{t}"'


def _tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", norm(q)) if t and t not in STOPWORDS]


def build_fts_query(query: str, mode: str = "and") -> str | None:
    """FTS5 MATCH expression: AND of query words (prefix match), OR'ed with known popular-name aliases."""
    toks = _tokens(query)
    joiner = " AND " if mode == "and" else " OR "
    clauses = []
    if toks:
        clauses.append("(" + joiner.join(_fts_term(t) for t in toks) + ")")
    for key in matched_aliases(query):
        for e in ALIASES[key]:
            et = _tokens(e)
            if et:
                clauses.append("(" + " AND ".join(_fts_term(t) for t in et) + ")")
    return " OR ".join(clauses) if clauses else None


@dataclass
class Acteur:
    uid: str
    civ: str | None
    prenom: str
    nom: str
    mandats: list[dict[str, Any]]  # legislature, date_debut, date_fin, departement, circo, groupes
    score: float = 0.0

    @property
    def name(self) -> str:
        return f"{self.prenom} {self.nom}".strip()

    @property
    def feminine(self) -> bool:
        return (self.civ or "").lower().startswith("mme")


def _in_office_sql(alias: str = "s") -> str:
    return (
        f"EXISTS (SELECT 1 FROM mandats m WHERE m.acteur = :uid AND m.legislature = {alias}.legislature "
        f"AND m.date_debut <= {alias}.date AND (m.date_fin IS NULL OR m.date_fin >= {alias}.date))"
    )


def _fmt_int(n: int | None) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ")


def _pct(a: int, b: int) -> str:
    return "n/a" if not b else f"{100 * a / b:.1f} %".replace(".", ",")


class AssembleeIndex:
    """Read-only access to the SQLite index (sync; wrap in a thread for async use)."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self._con: sqlite3.Connection | None = None
        self._acteurs: list[tuple[str, str | None, str, str, str, str]] | None = None
        self._groups: dict[str, tuple[str, str]] | None = None

    # -- infra
    @property
    def available(self) -> bool:
        return self.db_path.exists()

    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
        return self._con

    def meta(self) -> dict[str, str]:
        return dict(self.con().execute("SELECT key, value FROM meta").fetchall())

    def group_label(self, organe: str | None) -> str:
        if not organe:
            return "?"
        if self._groups is None:
            self._groups = {
                r["uid"]: (
                    r["abrev"] or r["abrege"] or r["libelle"] or r["uid"],
                    r["libelle"] or "",
                )
                for r in self.con().execute("SELECT uid, abrev, abrege, libelle FROM organes WHERE code_type='GP'")
            }
        return self._groups.get(organe, (organe, ""))[0]

    # -- deputies
    def _load_acteurs(self) -> list[tuple[str, str | None, str, str, str, str]]:
        if self._acteurs is None:
            self._acteurs = [
                tuple(r)
                for r in self.con().execute(
                    "SELECT uid, civ, prenom, nom, norm_full, norm_nom FROM acteurs WHERE is_depute = 1"
                )
            ]
        return self._acteurs

    def acteur(self, uid: str, as_of: str | None = None) -> Acteur | None:
        r = self.con().execute("SELECT uid, civ, prenom, nom FROM acteurs WHERE uid = ?", (uid,)).fetchone()
        if not r:
            return None
        return Acteur(r["uid"], r["civ"], r["prenom"], r["nom"], self.mandats(uid, as_of))

    def mandats(self, uid: str, as_of: str | None = None) -> list[dict[str, Any]]:
        out = []
        rows = self.con().execute("SELECT * FROM mandats WHERE acteur = ? ORDER BY date_debut", (uid,)).fetchall()
        gps = (
            self.con()
            .execute(
                "SELECT organe, legislature, date_debut, date_fin, qualite FROM gp_mandats WHERE acteur = ? ORDER BY date_debut",
                (uid,),
            )
            .fetchall()
        )
        for m in rows:
            if as_of and m["date_debut"] and m["date_debut"] > as_of:
                continue
            fin = m["date_fin"]
            if as_of and (fin is None or fin > as_of):
                fin = None
            hi = fin or "9999-12-31"
            spans: dict[str, list[str]] = {}  # label -> [first day, last day] within this mandate
            for g in gps:
                if g["legislature"] != m["legislature"]:
                    continue
                if as_of and g["date_debut"] and g["date_debut"] > as_of:
                    continue
                g0 = max(g["date_debut"] or "", m["date_debut"] or "")
                g1 = min(g["date_fin"] or "9999-12-31", hi)
                if g0 <= g1:
                    lab = self.group_label(g["organe"])
                    if g["qualite"] and "apparent" in g["qualite"].lower():
                        lab += " (apparenté)"
                    sp = spans.setdefault(lab, [g0, g1])
                    sp[0], sp[1] = min(sp[0], g0), max(sp[1], g1)

            # drop transient memberships (e.g. a few days as non-inscrit before groups are formed)
            def _days(sp: list[str]) -> int:
                try:
                    return (Date.fromisoformat(sp[1][:10]) - Date.fromisoformat(sp[0][:10])).days
                except ValueError:
                    return 9999

            items = sorted(spans.items(), key=lambda kv: kv[1][0])
            if len(items) > 1:
                items = [kv for kv in items if _days(kv[1]) >= 25] or items
            if len(items) == 1:
                groupes = [items[0][0]]
            else:
                groupes = [f"{lab} {sp[0]}→{'' if sp[1] >= hi[:10] else sp[1]}" for lab, sp in items]
            out.append(
                {
                    "legislature": m["legislature"],
                    "date_debut": m["date_debut"],
                    "date_fin": fin,
                    "departement": m["departement"],
                    "circo": m["circo"],
                    "groupes": groupes,
                }
            )
        return out

    def find_deputies(self, name: str, as_of: str | None = None, limit: int = 6) -> list[Acteur]:
        q = norm_name(name)
        q = re.sub(
            r"^(madame|monsieur|mme|mlle|mr|m|le depute|la deputee|depute|deputee)\s+",
            "",
            q,
        ).strip()
        if not q:
            return []
        if re.fullmatch(r"pa\d+", q):
            a = self.acteur(q.upper(), as_of)
            return [a] if a else []
        qtoks = q.split()
        scored: list[tuple[float, str]] = []
        for uid, _civ, prenom, nom, full, nnom in self._load_acteurs():
            ftoks = full.split()
            if q == full or q == norm_name(f"{nom} {prenom}"):
                sc = 100.0
            elif q == nnom:
                sc = 90.0
            elif all(t in ftoks for t in qtoks):
                sc = 80.0
            elif q in full:
                sc = 70.0
            else:
                r = max(
                    difflib.SequenceMatcher(None, q, full).ratio(),
                    difflib.SequenceMatcher(None, q, nnom).ratio(),
                )
                sc = r * 60 if r >= 0.78 else 0
            if sc:
                scored.append((sc, uid))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] >= 70:  # strong match: drop weak fuzzy ones
            scored = [x for x in scored if x[0] >= 70]
        elif scored:
            scored = [x for x in scored if x[0] >= scored[0][0] - 6]
        out: list[Acteur] = []
        for sc, uid in scored[: limit * 3]:
            a = self.acteur(uid, as_of)
            if a and a.mandats:
                a.score = sc
                out.append(a)
        # best score first, then most recent mandate
        out.sort(key=lambda a: (-a.score, -(max(m["legislature"] or 0 for m in a.mandats))))
        return out[:limit]

    def resolve(
        self,
        deputy: str,
        as_of: str | None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[Acteur | None, list[Acteur]]:
        """Return (acteur, candidates). acteur is None when not found or ambiguous."""
        cands = self.find_deputies(deputy, as_of)
        if not cands:
            return None, []
        top = [c for c in cands if c.score == cands[0].score]
        if len(top) > 1 and (date_from or date_to):
            lo, hi = date_from or "0000", date_to or "9999"
            top2 = [
                c
                for c in top
                if any((m["date_debut"] or "") <= hi and (m["date_fin"] or "9999") >= lo for m in c.mandats)
            ]
            top = top2 or top
        if len(top) == 1:
            return top[0], cands
        return None, cands

    def group_at(self, uid: str, day: str) -> str | None:
        r = (
            self.con()
            .execute(
                """SELECT organe FROM gp_mandats WHERE acteur = ? AND date_debut <= ? AND (date_fin IS NULL OR date_fin >= ?)
               ORDER BY (qualite LIKE 'Membre%' OR qualite LIKE 'Député non-inscrit%') DESC LIMIT 1""",
                (uid, day, day),
            )
            .fetchone()
        )
        return r[0] if r else None

    # -- scrutins
    def search(
        self,
        query: str,
        *,
        as_of: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        legislature: int | None = None,
        deputy_uid: str | None = None,
        whole_text_only: bool = False,
        limit: int = 8,
    ) -> tuple[list[sqlite3.Row], int, int, bool, int]:
        """Return (rows, n_total, n_whole_text, approximate, n_outside_mandate)."""
        where = ["1=1"]
        params: dict[str, Any] = {}
        if as_of:
            where.append("s.date <= :as_of")
            params["as_of"] = as_of
        if date_from:
            where.append("s.date >= :df")
            params["df"] = date_from
        if date_to:
            where.append("s.date <= :dt")
            params["dt"] = date_to
        if legislature:
            where.append("s.legislature = :leg")
            params["leg"] = legislature
        if whole_text_only:
            where.append(f"s.kind IN ({','.join(repr(k) for k in WHOLE_TEXT_KINDS)})")
        base_where = list(where)
        if deputy_uid:
            params["uid"] = deputy_uid
            where.append(_in_office_sql("s"))

        approximate = False
        fq = build_fts_query(query or "")
        con = self.con()
        rows: list[sqlite3.Row] = []

        def run(match: str | None, w: list[str]) -> list[sqlite3.Row]:
            if match:
                sql = (
                    "SELECT s.*, bm25(scrutins_fts) AS rank FROM scrutins_fts JOIN scrutins s ON s.id = scrutins_fts.rowid "
                    f"WHERE scrutins_fts MATCH :q AND {' AND '.join(w)}"
                )
                return con.execute(sql, {**params, "q": match}).fetchall()
            sql = f"SELECT s.*, 0.0 AS rank FROM scrutins s WHERE {' AND '.join(w)} ORDER BY s.date DESC LIMIT 2000"
            return con.execute(sql, params).fetchall()

        rows = run(fq, where)
        if not rows and fq and len(_tokens(query)) > 1:
            fq2 = build_fts_query(query, mode="or")
            rows = run(fq2, where)
            approximate = bool(rows)
        n_outside = 0
        if deputy_uid and fq:
            outside = run(fq if not approximate else build_fts_query(query, mode="or"), base_where)
            n_outside = max(0, len(outside) - len(rows))

        def key(r: sqlite3.Row) -> tuple[Any, ...]:
            rank = r["rank"] or 0.0
            if approximate:
                return (
                    round(rank, 0),
                    KIND_BUCKET.get(r["kind"], 4),
                    r["date"] and -int(r["date"].replace("-", "")),
                )
            return (
                KIND_BUCKET.get(r["kind"], 4),
                round(rank, 1),
                -int((r["date"] or "0").replace("-", "")),
            )

        rows.sort(key=key)
        n_whole = sum(1 for r in rows if r["kind"] in WHOLE_TEXT_KINDS)
        return rows[:limit], len(rows), n_whole, approximate, n_outside

    def group_votes(self, sid: int) -> list[sqlite3.Row]:
        return (
            self.con()
            .execute(
                "SELECT * FROM group_votes WHERE scrutin = ? ORDER BY nb_membres DESC",
                (sid,),
            )
            .fetchall()
        )

    def position(self, uid: str, sid: int) -> sqlite3.Row | None:
        return self.con().execute("SELECT * FROM positions WHERE acteur = ? AND scrutin = ?", (uid, sid)).fetchone()

    def corrections(self, uid: str, sid: int) -> list[sqlite3.Row]:
        return self.con().execute("SELECT * FROM corrections WHERE acteur = ? AND scrutin = ?", (uid, sid)).fetchall()

    def in_office(self, uid: str, legislature: int, day: str) -> bool:
        return bool(
            self.con()
            .execute(
                "SELECT 1 FROM mandats WHERE acteur = ? AND legislature = ? AND date_debut <= ? "
                "AND (date_fin IS NULL OR date_fin >= ?)",
                (uid, legislature, day, day),
            )
            .fetchone()
        )

    def deputy_position(self, uid: str, s: sqlite3.Row) -> dict[str, Any]:
        """Position of a deputy on a scrutin, including the 'absent' inference."""
        p = self.position(uid, s["id"])
        corr = self.corrections(uid, s["id"])
        out: dict[str, Any] = {
            "position": None,
            "delegation": False,
            "groupe": None,
            "cause": None,
            "corrections": corr,
        }
        if p:
            out.update(
                position=p["position"],
                delegation=bool(p["delegation"]),
                groupe=p["groupe"],
                cause=p["cause"],
            )
            return out
        if not self.in_office(uid, s["legislature"], s["date"]):
            out["position"] = "hors_mandat"
            return out
        out["groupe"] = self.group_at(uid, s["date"])
        if s["kind"] == "censure":
            out["position"] = "pas_pour_censure"
        elif not s["nominatif"]:
            out["position"] = "inconnue"
        else:
            out["position"] = "absent"
        return out

    def stats(self, uid: str, date_from: str | None, date_to: str | None, as_of: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "uid": uid,
            "df": date_from or "0000-00-00",
            "dt": date_to or "9999-12-31",
        }
        hi = "9999-12-31"
        if as_of:
            hi = min(params["dt"], as_of)
        params["dt"] = hi
        sql = f"""
          SELECT s.legislature AS leg, min(s.date) AS d0, max(s.date) AS d1, count(*) AS n,
            sum(p.position = 'pour') AS pour, sum(p.position = 'contre') AS contre,
            sum(p.position = 'abstention') AS abst, sum(p.position = 'non_votant') AS nv,
            sum(p.delegation) AS deleg, sum(p.position IS NULL) AS absent,
            sum(s.type_code IN ('SPS','SSG')) AS n_sol,
            sum(s.type_code IN ('SPS','SSG') AND p.position IN ('pour','contre','abstention')) AS sol_voted,
            sum(s.kind = 'ensemble') AS n_ens,
            sum(s.kind = 'ensemble' AND p.position IN ('pour','contre','abstention')) AS ens_voted,
            sum(s.n_votes) AS all_votes, sum(s.n_exercice) AS all_seats
          FROM scrutins s LEFT JOIN positions p ON p.acteur = :uid AND p.scrutin = s.id
          WHERE s.date >= :df AND s.date <= :dt AND s.nominatif = 1 AND s.kind != 'censure' AND {_in_office_sql("s")}
          GROUP BY s.legislature ORDER BY s.legislature"""
        return [dict(r) for r in self.con().execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def scrutin_url(legislature: int, numero: int) -> str:
    return f"https://www.assemblee-nationale.fr/dyn/{legislature}/scrutins/{numero}"


def deputy_url(uid: str) -> str:
    return f"https://www.assemblee-nationale.fr/dyn/deputes/{uid}"


def _result_str(s: sqlite3.Row) -> str:
    sort = s["sort"] or "?"
    if s["kind"] == "censure":
        return f"{sort} ({_fmt_int(s['pour'])} voix pour la censure, majorité requise {_fmt_int(s['requis'])})"
    return f"{sort} (pour {_fmt_int(s['pour'])}, contre {_fmt_int(s['contre'])}, abst. {_fmt_int(s['abstentions'])})"


def _titre(s: sqlite3.Row, n: int = 200) -> str:
    t = short_title(s["titre"], n)
    ctx = CENSURE_CONTEXT.get((s["legislature"], s["numero"]))
    return f"{t} [{ctx}]" if ctx else t


def _kind_str(s: sqlite3.Row) -> str:
    k = KIND_LABEL.get(s["kind"], s["kind"])
    if s["type_code"] in ("SPS", "SSG"):
        k += ", scrutin solennel"
    return k


def _mandats_str(a: Acteur) -> str:
    parts = []
    for m in a.mandats:
        fin = m["date_fin"] or "en cours"
        lieu = f"{m['departement']} {m['circo']}e circ." if m["departement"] else ""
        grp = "/".join(m["groupes"]) if m["groupes"] else "?"
        parts.append(f"L{m['legislature']} {m['date_debut']}→{fin} {lieu} [{grp}]".replace("  ", " "))
    return "; ".join(parts) if parts else "aucun mandat de député (législatures 11+)"


def _group_line(idx: AssembleeIndex, gv: sqlite3.Row, censure: bool = False) -> str:
    lab = idx.group_label(gv["organe"])
    if censure:
        return f"{lab}: {gv['pour'] or 0} pour la censure/{gv['nb_membres'] or '?'}"
    pos = gv["position"] or "?"
    return f"{lab}: {pos} ({gv['pour'] or 0}-{gv['contre'] or 0}-{gv['abstentions'] or 0})"


def _position_text(idx: AssembleeIndex, a: Acteur, s: sqlite3.Row, dp: dict[str, Any]) -> str:
    pos = dp["position"]
    e = "e" if a.feminine else ""
    if pos in POS_LABEL:
        txt = POS_LABEL[pos]
        if pos == "non_votant" and dp.get("cause"):
            txt += f" – {CAUSE_LABEL.get(dp['cause'], dp['cause'])}"
        if dp.get("delegation"):
            txt += " (par délégation : vote confié à un collègue)"
    elif pos == "absent":
        txt = f"ABSENT{e.upper()} (en exercice, non inscrit{e} au décompte)"
    elif pos == "pas_pour_censure":
        txt = "N'A PAS VOTÉ LA CENSURE (seuls les votes pour sont recensés)"
    elif pos == "inconnue":
        txt = "NON PUBLIÉE (seuls les dissidents sont listés : a suivi son groupe ou était absent)"
    elif pos == "hors_mandat":
        txt = f"PAS DÉPUTÉ{e.upper()} à cette date"
    else:
        txt = str(pos)
    for c in dp.get("corrections") or []:
        txt += f" [mise au point{' (dysfonctionnement)' if c['dysfonctionnement'] else ''} : souhaitait voter {c['position']}]"
    return txt


def _source(s: sqlite3.Row, excerpt: str) -> Source:
    return Source(
        kind="parliament",
        publisher=PUBLISHER,
        title=f"Scrutin public n° {s['numero']} – {short_title(s['titre'], 90)}",
        url=scrutin_url(s["legislature"], s["numero"]),
        date=s["date"],
        excerpt=excerpt[:300],
    )


def _clip_n(lines: list[str], footer: list[str] | None = None, limit: int = MAX_CONTENT) -> tuple[str, int]:
    """Join lines under the size budget; returns (text, number of lines kept)."""
    footer = footer or []
    out: list[str] = []
    budget = limit - sum(len(f) + 1 for f in footer) - 40
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > budget:
            out.append(
                f"… ({len(lines) - len(out)} résultat(s) non affiché(s), réduire 'limit' ou préciser la recherche)"
            )
            break
        out.append(ln)
        used += len(ln) + 1
    kept = len(out) if not out or not out[-1].startswith("… (") else len(out) - 1
    return "\n".join(out + footer), kept


def _clip(lines: list[str], footer: list[str] | None = None, limit: int = MAX_CONTENT) -> str:
    return _clip_n(lines, footer, limit)[0]


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------

_DATE_PROP = {
    "type": "string",
    "description": "Date ISO AAAA-MM-JJ (ou AAAA / AAAA-MM).",
}

TOOLS = [
    ToolSpec(
        name="an_find_deputy",
        description=(
            "Assemblée nationale : trouve un député (législatures 11 à 17, 2002-aujourd'hui) par son nom, "
            "insensible aux accents. Renvoie l'identifiant (uid PAxxxx), les mandats (législature, dates, "
            "circonscription, groupe politique). Utiliser pour vérifier si quelqu'un était député à une date, "
            "son groupe, ou pour lever une homonymie avant an_search_votes / an_deputy_stats."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nom (ex. « Le Pen », « Marine Le Pen ») ou uid PAxxxx.",
                }
            },
            "required": ["name"],
        },
    ),
    ToolSpec(
        name="an_search_votes",
        description=(
            "Assemblée nationale : votes d'UN député aux scrutins publics (2012-aujourd'hui) sur un sujet. "
            "Pour vérifier « j'ai voté contre… », « vous avez voté pour… », « elle n'a pas voté la loi X ». "
            "Recherche plein texte dans les intitulés officiels des scrutins (insensible aux accents) : utiliser "
            "les mots du titre officiel de la loi (ex. « crise sanitaire » plutôt que « passe vaccinal », "
            "« système universel de retraite »), quelques alias courants sont gérés. Renvoie pour chaque "
            "scrutin : la position du député (pour / contre / abstention / non-votant / ABSENT = en exercice "
            "mais non inscrit au décompte), le résultat, la position majoritaire de son groupe, et le type de "
            "vote. « Voter contre une loi » = en général le VOTE SUR L'ENSEMBLE DU TEXTE (listés en premier), "
            "pas un amendement ou un article. Les motions de censure ne citent souvent pas le texte : chercher "
            "« motion de censure » avec une période. Seuls les scrutins pendant ses mandats sont listés."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deputy": {
                    "type": "string",
                    "description": "Nom du député ou uid PAxxxx (voir an_find_deputy).",
                },
                "query": {
                    "type": "string",
                    "description": "Mots-clés du titre du texte/scrutin (ex. « crise sanitaire »).",
                },
                "date_from": _DATE_PROP,
                "date_to": _DATE_PROP,
                "whole_text_only": {
                    "type": "boolean",
                    "description": "Ne garder que les votes sur l'ensemble d'un texte, motions de censure, déclarations, résolutions.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Nombre de scrutins (défaut 8).",
                },
            },
            "required": ["deputy", "query"],
        },
    ),
    ToolSpec(
        name="an_search_scrutins",
        description=(
            "Assemblée nationale : recherche de scrutins publics (2012-aujourd'hui) par mots-clés du titre "
            "officiel, avec le résultat (adopté/rejeté, décompte) et la position majoritaire de CHAQUE GROUPE "
            "politique (RN, LFI, LREM/EPR, LR, SOC…, format groupe: position (pour-contre-abst)). Pour vérifier "
            "« le RN n'a pas voté la loi X », « adopté à l'unanimité », « la gauche a voté contre ». Les votes "
            "sur l'ensemble du texte sont listés en premier. Utiliser les mots du titre officiel."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Mots-clés du titre (ex. « dérèglement climatique résilience »).",
                },
                "legislature": {
                    "type": "integer",
                    "enum": [14, 15, 16, 17],
                    "description": "14=2012-17, 15=2017-22, 16=2022-24, 17=2024-.",
                },
                "date_from": _DATE_PROP,
                "date_to": _DATE_PROP,
                "whole_text_only": {
                    "type": "boolean",
                    "description": "Seulement les votes sur l'ensemble d'un texte / censure / déclarations.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 15,
                    "description": "Nombre de scrutins (défaut 6).",
                },
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="an_deputy_stats",
        description=(
            "Assemblée nationale : participation d'un député aux scrutins publics sur une période (nombre de "
            "scrutins pendant son mandat, votés, absences, votes par délégation, scrutins solennels, moyenne "
            "des députés). Pour les affirmations d'assiduité/absentéisme. Attention : mesure partielle (votes "
            "à main levée non nominatifs, travail en commission non compté)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deputy": {
                    "type": "string",
                    "description": "Nom du député ou uid PAxxxx.",
                },
                "date_from": _DATE_PROP,
                "date_to": _DATE_PROP,
            },
            "required": ["deputy"],
        },
    ),
]


class AssembleeConnector:
    name = "assemblee"

    def __init__(self, db_path: Path | str | None = None):
        self.index = AssembleeIndex(Path(db_path) if db_path else DB_PATH)

    def tools(self) -> list[ToolSpec]:
        return list(TOOLS)

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        if not self.index.available:
            return ToolResult(
                content=(
                    "Index Assemblée nationale indisponible (lancer `uv run python -m sic.sources.assemblee build`)."
                )
            )
        fn = {
            "an_find_deputy": self._find_deputy,
            "an_search_votes": self._search_votes,
            "an_search_scrutins": self._search_scrutins,
            "an_deputy_stats": self._deputy_stats,
        }.get(tool)
        if fn is None:
            return ToolResult(content=f"Outil inconnu pour le connecteur assemblee : {tool}")
        try:
            return await asyncio.to_thread(fn, args or {}, ctx)
        except ValueError as e:
            return ToolResult(content=f"Erreur d'argument : {e}")
        except Exception as e:  # noqa: BLE001 - errors go back to the LLM as text
            return ToolResult(content=f"Erreur lors de la requête Assemblée nationale ({tool}) : {e!r}")

    # -- helpers
    @staticmethod
    def _as_of(ctx: VerifyContext | None) -> str | None:
        return ctx.as_of.isoformat() if ctx and ctx.as_of else None

    def _resolve(self, args: dict[str, Any], ctx: VerifyContext, df: str | None, dt: str | None):
        deputy = str(args.get("deputy") or "").strip()
        as_of = self._as_of(ctx)
        if not deputy and ctx and ctx.speaker and ctx.speaker.an_id:
            deputy = ctx.speaker.an_id
        if (
            ctx
            and ctx.speaker
            and ctx.speaker.an_id
            and norm_name(deputy)
            in {
                norm_name(ctx.speaker.id),
                norm_name(ctx.speaker.name),
                *(norm_name(x) for x in ctx.speaker.aliases),
            }
        ):
            deputy = ctx.speaker.an_id
        if not deputy:
            raise ValueError("paramètre 'deputy' manquant")
        a, cands = self.index.resolve(deputy, as_of, df, dt)
        if a is None:
            if not cands:
                return None, ToolResult(
                    content=f"Aucun député trouvé pour « {deputy} » (Assemblée nationale, législatures 11 à 17"
                    + (f", jusqu'au {as_of}" if as_of else "")
                    + "). Cette personne n'a peut-être jamais été députée."
                )
            lines = [f"Plusieurs députés correspondent à « {deputy} » ; préciser l'uid :"]
            lines += [f"- {c.uid} {c.name} : {_mandats_str(c)}" for c in cands]
            return None, ToolResult(content=_clip(lines))
        return a, None

    # -- tools
    def _find_deputy(self, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            raise ValueError("paramètre 'name' manquant")
        as_of = self._as_of(ctx)
        cands = self.index.find_deputies(name, as_of)
        if not cands:
            return ToolResult(
                content=f"Aucun député trouvé pour « {name} » (législatures 11 à 17"
                + (f", mandats commencés avant le {as_of}" if as_of else "")
                + ")."
            )
        lines = [f"Députés correspondant à « {name} »" + (f" (données au {as_of})" if as_of else "") + " :"]
        sources = []
        for c in cands:
            lines.append(f"- {c.uid} · {c.name} · {_mandats_str(c)}")
            sources.append(
                Source(
                    kind="parliament",
                    publisher=PUBLISHER,
                    title=f"Fiche député – {c.name}",
                    url=deputy_url(c.uid),
                    excerpt=_mandats_str(c)[:300],
                )
            )
        return ToolResult(content=_clip(lines), sources=sources[:3])

    def _search_votes(self, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        df, dt = (
            _parse_date(args.get("date_from")),
            _parse_date(args.get("date_to"), end=True),
        )
        query = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 8), 20))
        as_of = self._as_of(ctx)
        a, err = self._resolve(args, ctx, df, dt)
        if err:
            return err
        assert a is not None
        rows, n_total, n_whole, approx, n_out = self.index.search(
            query,
            as_of=as_of,
            date_from=df,
            date_to=dt,
            deputy_uid=a.uid,
            whole_text_only=bool(args.get("whole_text_only")),
            limit=limit,
        )
        head = [
            f"Député{'e' if a.feminine else ''} : {a.name} ({a.uid}) – mandats : {_mandats_str(a)}",
            f"Recherche « {query} »"
            + (f" du {df}" if df else "")
            + (f" au {dt}" if dt else "")
            + (f" (données jusqu'au {as_of})" if as_of else "")
            + f" : {n_total} scrutin(s) pendant ses mandats, dont {n_whole} sur l'ensemble d'un texte/censure/déclaration."
            + (f" {n_out} autre(s) scrutin(s) correspondant(s) hors de ses mandats." if n_out else ""),
        ]
        if matched_aliases(query):
            head.append("Alias inclus (titres officiels) : " + _alias_note(query))
        if approx:
            head.append("Aucun scrutin ne contient tous les mots : résultats approchés (au moins un mot).")
        if not rows:
            head.append(
                "Aucun scrutin public correspondant pendant ses mandats (essayer d'autres mots du titre officiel)."
            )
            return ToolResult(content=_clip(head))
        lines, sources = [], []
        for s in rows:
            dp = self.index.deputy_position(a.uid, s)
            ptxt = _position_text(self.index, a, s, dp)
            gv = None
            if dp.get("groupe"):
                gv = next(
                    (g for g in self.index.group_votes(s["id"]) if g["organe"] == dp["groupe"]),
                    None,
                )
            gtxt = ""
            if gv is not None:
                gtxt = f" | son groupe {_group_line(self.index, gv, s['kind'] == 'censure')}"
                if s["kind"] != "censure":
                    gtxt = f" | son groupe ({self.index.group_label(gv['organe'])}) : majorité {gv['position']} ({gv['pour'] or 0}-{gv['contre'] or 0}-{gv['abstentions'] or 0})"
            lines.append(
                f"- {s['date']} · n° {s['numero']} (L{s['legislature']}) · {_kind_str(s)} · « {_titre(s)} »\n"
                f"  → {a.name} : {ptxt} | résultat : {_result_str(s)}{gtxt}"
            )
            sources.append(_source(s, f"{a.name} : {ptxt.split(' (')[0]} ; {_result_str(s)}"))
        footer = [
            (
                "Légende : groupe (pour-contre-abst.). ABSENT = en exercice mais absent du décompte nominatif (absent ou "
                "présent sans voter)."
            )
        ]
        text, kept = _clip_n(head + lines, footer)
        return ToolResult(content=text, sources=sources[: max(0, kept - len(head))])

    def _search_scrutins(self, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        df, dt = (
            _parse_date(args.get("date_from")),
            _parse_date(args.get("date_to"), end=True),
        )
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("paramètre 'query' manquant")
        limit = max(1, min(int(args.get("limit") or 6), 15))
        leg = args.get("legislature")
        leg = int(leg) if leg not in (None, "") else None
        as_of = self._as_of(ctx)
        rows, n_total, n_whole, approx, _ = self.index.search(
            query,
            as_of=as_of,
            date_from=df,
            date_to=dt,
            legislature=leg,
            whole_text_only=bool(args.get("whole_text_only")),
            limit=limit,
        )
        head = [
            f"Scrutins publics « {query} »"
            + (f" L{leg}" if leg else "")
            + (f" du {df}" if df else "")
            + (f" au {dt}" if dt else "")
            + (f" (données jusqu'au {as_of})" if as_of else "")
            + f" : {n_total} trouvé(s), dont {n_whole} sur l'ensemble d'un texte/censure/déclaration."
        ]
        if matched_aliases(query):
            head.append("Alias inclus (titres officiels) : " + _alias_note(query))
        if approx:
            head.append("Aucun scrutin ne contient tous les mots : résultats approchés (au moins un mot).")
        if not rows:
            head.append("Aucun scrutin public trouvé (essayer d'autres mots du titre officiel).")
            return ToolResult(content=_clip(head))
        lines, sources = [], []
        for s in rows:
            gvs = self.index.group_votes(s["id"])
            censure = s["kind"] == "censure"
            groups = " · ".join(_group_line(self.index, g, censure) for g in gvs)
            unanim = ""
            if not censure and s["sort"] == "adopté" and (s["contre"] or 0) == 0:
                unanim = " [aucune voix contre" + (", aucune abstention]" if (s["abstentions"] or 0) == 0 else "]")
            lines.append(
                f"- {s['date']} · n° {s['numero']} (L{s['legislature']}) · {_kind_str(s)} · « {_titre(s)} »\n"
                f"  → {_result_str(s)}{unanim} ; votants {_fmt_int(s['votants'])}\n  groupes : {groups}"
            )
            sources.append(_source(s, f"{_result_str(s)} ; {groups}"))
        footer = ["Format groupe : position majoritaire (pour-contre-abst.)."]
        text, kept = _clip_n(head + lines, footer)
        return ToolResult(content=text, sources=sources[: max(0, kept - len(head))])

    def _deputy_stats(self, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        df, dt = (
            _parse_date(args.get("date_from")),
            _parse_date(args.get("date_to"), end=True),
        )
        as_of = self._as_of(ctx)
        a, err = self._resolve(args, ctx, df, dt)
        if err:
            return err
        assert a is not None
        st = self.index.stats(a.uid, df, dt, as_of)
        e = "e" if a.feminine else ""
        lines = [
            f"{a.name} ({a.uid}) – participation aux scrutins publics nominatifs"
            + (f" du {df}" if df else "")
            + (f" au {dt}" if dt else "")
            + (f" (données jusqu'au {as_of})" if as_of else "")
            + f". Mandats : {_mandats_str(a)}"
        ]
        if not st:
            lines.append(
                f"Aucun scrutin public nominatif pendant ses mandats sur la période (pas député{e} ou données absentes)."
            )
        tot_n = tot_v = 0
        for r in st:
            voted = (r["pour"] or 0) + (r["contre"] or 0) + (r["abst"] or 0)
            tot_n += r["n"]
            tot_v += voted
            lines.append(
                f"- L{r['leg']} ({r['d0']} → {r['d1']}) : {_fmt_int(r['n'])} scrutins pendant son mandat ; "
                f"a voté à {_fmt_int(voted)} ({_pct(voted, r['n'])}) – pour {r['pour'] or 0}, contre {r['contre'] or 0}, "
                f"abstention {r['abst'] or 0} ; non-votant {r['nv'] or 0} ; non inscrit{e} au décompte (absent{e}) "
                f"{_fmt_int(r['absent'] or 0)} ; dont votes par délégation {r['deleg'] or 0}.\n"
                f"  Scrutins solennels : {r['n_sol'] or 0}, votés {r['sol_voted'] or 0} ({_pct(r['sol_voted'] or 0, r['n_sol'] or 0)}). "
                f"Votes sur l'ensemble d'un texte : {r['n_ens'] or 0}, votés {r['ens_voted'] or 0} "
                f"({_pct(r['ens_voted'] or 0, r['n_ens'] or 0)}). "
                f"Moyenne des députés sur ces mêmes scrutins : {_pct(r['all_votes'] or 0, r['all_seats'] or 0)}."
            )
        if len(st) > 1:
            lines.append(f"Total : {_fmt_int(tot_v)} votes sur {_fmt_int(tot_n)} scrutins ({_pct(tot_v, tot_n)}).")
        footer = [
            (
                "Prudence : mesure partielle de l'assiduité. Beaucoup de votes se font à main levée (non nominatifs) ; "
                "les scrutins publics ordinaires ont souvent lieu avec peu de présents ; le travail en commission, les "
                "questions et la circonscription ne sont pas comptés. Motions de censure exclues ; scrutins de 2012-2014 "
                "publiés en « dissidents seulement » exclus."
            )
        ]
        src = Source(
            kind="parliament",
            publisher=PUBLISHER,
            title=f"Fiche député – {a.name} (votes)",
            url=deputy_url(a.uid),
            date=as_of,
            excerpt=(lines[1] if len(lines) > 1 else lines[0])[:300],
        )
        return ToolResult(content=_clip(lines, footer), sources=[src])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sic.sources.assemblee")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="download open data and build the SQLite index")
    b.add_argument("--force", action="store_true", help="rebuild even if sources are unchanged")
    b.add_argument(
        "--refresh",
        action="store_true",
        help="re-check upstream files of the current legislature/AMO",
    )
    b.add_argument("--offline", action="store_true", help="use only already-downloaded files")
    for name in ("deputy", "votes", "scrutins", "stats"):
        p = sub.add_parser(name)
        if name in ("deputy", "votes", "stats"):
            p.add_argument("who")
        if name in ("votes", "scrutins"):
            p.add_argument("query")
            p.add_argument("--limit", type=int)
            p.add_argument("--whole", action="store_true")
        if name == "scrutins":
            p.add_argument("--leg", type=int)
        p.add_argument("--from", dest="date_from")
        p.add_argument("--to", dest="date_to")
        p.add_argument("--as-of", dest="as_of")
        p.add_argument("--db", default=str(DB_PATH))
    a = ap.parse_args(argv)
    if a.cmd == "build":
        info = build(force=a.force, refresh=a.refresh, offline=a.offline)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0
    conn = AssembleeConnector(a.db)
    ctx = VerifyContext(as_of=Date.fromisoformat(a.as_of) if a.as_of else None)
    if a.cmd == "deputy":
        tool, args = "an_find_deputy", {"name": a.who}
    elif a.cmd == "votes":
        tool, args = (
            "an_search_votes",
            {
                "deputy": a.who,
                "query": a.query,
                "limit": a.limit,
                "whole_text_only": a.whole,
            },
        )
    elif a.cmd == "scrutins":
        tool, args = (
            "an_search_scrutins",
            {
                "query": a.query,
                "legislature": a.leg,
                "limit": a.limit,
                "whole_text_only": a.whole,
            },
        )
    else:
        tool, args = "an_deputy_stats", {"deputy": a.who}
    args.update({"date_from": a.date_from, "date_to": a.date_to})
    res = asyncio.run(conn.call(tool, {k: v for k, v in args.items() if v not in (None, False)}, ctx))
    print(res.content)
    print(f"\n[{len(res.content)} chars, {len(res.sources)} sources]")
    for s in res.sources[:3]:
        print(" ", s.model_dump_json(exclude_none=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
