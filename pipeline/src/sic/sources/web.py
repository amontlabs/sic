"""Web search + page reading through z.ai MCP servers (web_search_prime, web_reader) and Brave.

Results are filtered:
- `as_of` (strict live simulation): drop results published after the claim date, drop undated
  results that are not from official domains, and drop pages that discuss the debate itself
  (leak guard). The publication date is taken, in order, from the engine's explicit date, the
  URL (/2022/04/21/, -20220421, …), a leading "Apr 21, 2022 —" snippet date, and only then from
  dates found elsewhere in the text (which can only prove a page is *too recent*).
- domains are tagged trusted / fact-checker / other; the verifier prefers trusted ones.
- page fetches refuse non-http(s) URLs and private/loopback/link-local hosts (SSRF), hop by hop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as Date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse, urlsplit

import httpx

from sic import config
from sic.models import Source, SourceKind
from sic.sources.base import ToolResult, ToolSpec, VerifyContext

TRUSTED = (
    "insee.fr", "gouv.fr", "assemblee-nationale.fr", "senat.fr", "vie-publique.fr", "ccomptes.fr",
    "banque-france.fr", "europa.eu", "oecd.org", "imf.org", "worldbank.org", "conseil-constitutionnel.fr",
    "conseil-etat.fr", "ined.fr", "francetravail.fr", "pole-emploi.org", "lassuranceretraite.fr",
    "cor-retraites.fr", "ademe.fr", "rte-france.com", "cre.fr", "iea.org", "ofii.fr", "ofpra.gouv.fr",
    "fipeco.fr", "insee.fr", "un.org", "who.int", "ecb.europa.eu", "eurostat.ec.europa.eu", "data.gouv.fr",
    "irdes.fr", "edf.fr", "ipcc.ch", "citepa.org", "cnil.fr",
)
FACTCHECKERS = (
    "lemonde.fr/les-decodeurs", "liberation.fr/checknews", "factuel.afp.com", "francetvinfo.fr/vrai-ou-fake",
    "tf1info.fr", "20minutes.fr/fake-off", "lci.fr", "francetvinfo.fr",
)

PROVIDER_TIMEOUT = 15.0  # per search provider, well under the verifier's per-tool timeout
BRAVE_MAX_QUERIES = 2  # Brave queries per web_search call
BRAVE_MAX_WAIT = 8.0  # skip Brave rather than queue longer than this behind other searches
MAX_REDIRECTS = 5
MAX_FETCH_BYTES = 5_000_000


def classify(url: str) -> str:
    u = url.lower()
    host = urlparse(u).hostname or ""
    if any(f in u for f in FACTCHECKERS[:6]):
        return "factcheck"
    if any(host == d or host.endswith("." + d) for d in TRUSTED):
        return "trusted"
    if any(f in u for f in FACTCHECKERS):
        return "factcheck"
    return "other"


def source_kind(url: str) -> SourceKind:
    c = classify(url)
    host = urlparse(url.lower()).hostname or ""
    if "assemblee-nationale.fr" in host or "senat.fr" in host:
        return "parliament"
    if "legifrance" in host:
        return "law"
    if c == "factcheck":
        return "factcheck"
    if c == "trusted":
        return "official_data"
    return "web"


# --------------------------------------------------------------------------------------
# Text normalisation (accent/case-insensitive matching)


def normalize(text: str) -> str:
    """Lowercase, strip accents, turn every non-alphanumeric run into one space."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^0-9a-z]+", " ", t).strip()


# --------------------------------------------------------------------------------------
# Dates

_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTHS_FR = {
    "janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6,
    "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "oct": 10, "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}
_DATE_EN = re.compile(r"^\s*([A-Z][a-z]{2})[a-z]* (\d{1,2}), (\d{4})")
_DATE_ANY = re.compile(r"(?<!\d)(20\d\d|19\d\d)-(\d\d)-(\d\d)(?!\d)")
_DATE_AGO = re.compile(r"^\s*(\d{1,3}) (minute|hour|day|week|month|year)s? ago\b")
_FR_MONTH_RE = "|".join(sorted(_MONTHS_FR, key=len, reverse=True))
_DATE_FR_PUBLISHED = re.compile(
    r"\b(?:publie|mis a jour|mise a jour|modifie)(?: le)?(?: (?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche))?"
    rf" (\d{{1,2}})(?: ?er)? (?:({_FR_MONTH_RE})|(\d{{1,2}})) (\d{{4}})\b"
)
_DATE_FR_LEADING = re.compile(rf"^(\d{{1,2}})(?: ?er)? ({_FR_MONTH_RE}) (\d{{4}})\b")  # on normalize()d text


def _mkdate(y: int | str, m: int | str, d: int | str) -> Date | None:
    try:
        out = Date(int(y), int(m), int(d))
    except ValueError:
        return None
    return out if 1990 <= out.year <= today().year + 1 else None


def leading_date(text: str) -> Date | None:
    """Date at the very start of a snippet: "Apr 11, 2022 — …", "3 days ago — …", "11 avr. 2022 — …"."""
    text = text or ""
    m = _DATE_EN.match(text)
    if m and m.group(1).lower() in _MONTHS:
        return _mkdate(m.group(3), _MONTHS[m.group(1).lower()], m.group(2))
    m = _DATE_AGO.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return today() - timedelta(days=days)
    m = _DATE_FR_LEADING.match(normalize(text[:40]))
    if m:
        return _mkdate(m.group(3), _MONTHS_FR[m.group(2)], m.group(1))
    m = re.match(r"^\s*(\d{4})-(\d\d)-(\d\d)(?!\d)", text)
    return _mkdate(*m.groups()) if m else None


def parse_date(text: str) -> Date | None:
    """Best-effort single date: a leading date, an RFC 2822 date, an ISO date anywhere, or "publié le …"."""
    text = text or ""
    d = leading_date(text)
    if d:
        return d
    with contextlib.suppress(TypeError, ValueError, IndexError):
        if re.match(r"^\s*[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}", text):
            return parsedate_to_datetime(text.strip()).date()
    m = _DATE_ANY.search(text)
    if m:
        return _mkdate(*m.groups())
    return published_in_text(text)


def published_in_text(text: str) -> Date | None:
    """French "Publié le 12 avril 2022" / "Mis à jour le 12/04/2022" mentions."""
    m = _DATE_FR_PUBLISHED.search(normalize(text))
    if not m:
        return None
    month = _MONTHS_FR[m.group(2)] if m.group(2) else int(m.group(3))
    return _mkdate(m.group(4), month, m.group(1))


def _iso_dates(text: str) -> list[Date]:
    return [d for d in (_mkdate(*m.groups()) for m in _DATE_ANY.finditer(text or "")) if d]


_URL_EXACT = (
    re.compile(r"/((?:19|20)\d\d)/(\d{1,2})/(\d{1,2})(?=$|[/_.-])"),  # /2022/04/21/
    re.compile(r"(?<!\d)((?:19|20)\d\d)-(\d\d)-(\d\d)(?!\d)"),  # /2022-04-21 , -2022-04-21-
    re.compile(r"(?<![0-9A-Za-z])((?:19|20)\d\d)(\d\d)(\d\d)(?!\d)"),  # _20220421_ , -20220421 , /20220421ARTFIG
)
_URL_DMY = re.compile(r"(?<!\d)(\d\d)-(\d\d)-((?:19|20)\d\d)(?!\d)")  # leparisien: -21-04-2022-
_URL_MONTH = re.compile(r"/((?:19|20)\d\d)[/-](\d\d)(?=/)")  # /2022/04/ , /2022-04/
_URL_YEAR = re.compile(r"/((?:19|20)\d\d)(?=/)")  # /2021/


def _month_end(y: int, m: int) -> Date:
    return (Date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


def url_date_range(url: str) -> tuple[Date, Date] | None:
    """Publication date embedded in a URL path: exact day, else month, else year (as a [lo, hi] range)."""
    path = unquote(urlsplit(url or "").path)
    for rx in _URL_EXACT:
        for m in rx.finditer(path):
            d = _mkdate(*m.groups())
            if d:
                return d, d
    for m in _URL_DMY.finditer(path):
        d = _mkdate(m.group(3), m.group(2), m.group(1))
        if d:
            return d, d
    m = _URL_MONTH.search(path)
    if m and 1 <= int(m.group(2)) <= 12 and _mkdate(m.group(1), m.group(2), 1):
        y, mo = int(m.group(1)), int(m.group(2))
        return Date(y, mo, 1), _month_end(y, mo)
    m = _URL_YEAR.search(path)
    if m and _mkdate(m.group(1), 1, 1):
        y = int(m.group(1))
        return Date(y, 1, 1), Date(y, 12, 31)
    return None


def url_date(url: str) -> Date | None:
    r = url_date_range(url)
    return r[0] if r and r[0] == r[1] else None


@dataclass(frozen=True)
class PubDate:
    """Publication date as a [lo, hi] range plus where it came from."""

    lo: Date
    hi: Date
    origin: Literal["meta", "url", "snippet", "text"]

    @property
    def exact(self) -> bool:
        return self.lo == self.hi

    @property
    def label(self) -> str:
        if self.exact:
            return self.lo.isoformat()
        if (self.lo.year, self.lo.month) == (self.hi.year, self.hi.month):
            return self.lo.strftime("%Y-%m")
        return str(self.lo.year)


def publication_date(publish_date: str | None, url: str, text: str = "") -> PubDate | None:
    """Explicit date > URL date > leading snippet date / "publié le" > URL month/year > other dates."""
    d = parse_date(publish_date or "")
    if d:
        return PubDate(d, d, "meta")
    rng = url_date_range(url)
    if rng and rng[0] == rng[1]:
        return PubDate(rng[0], rng[1], "url")
    d = leading_date(text) or published_in_text(text[:2000])
    if d:
        return PubDate(d, d, "snippet")
    if rng:
        return PubDate(rng[0], rng[1], "url")
    dates = _iso_dates(text[:2000])
    if dates:  # a date mentioned in the text only proves the page is not older than it
        return PubDate(max(dates), max(dates), "text")
    return None


Status = Literal["before", "after", "unknown"]


def strict_status(pd: PubDate | None, as_of: Date) -> Status:
    if pd is None:
        return "unknown"
    if pd.lo > as_of:
        return "after"
    if pd.origin == "text" or pd.hi > as_of:
        return "unknown"
    return "before"


# --------------------------------------------------------------------------------------
# Leak guard (pages about the debate itself)

WEAK_LEAK_TERMS = frozenset(
    normalize(t) for t in ("hier soir", "lors du débat", "pendant le débat", "au cours du débat", "face-à-face", "à l'issue du débat")
)
# Common pre-debate uses of "débat" that must not trigger "débat … <candidat>" / "lors du débat".
_LEAK_EXEMPT = re.compile(
    r"\b(?:grand debat national|grand debat|debat national|debat parlementaire|debat public|debat d orientation"
    r"|debats parlementaires|debat budgetaire|debat sans vote)\b"
)


class LeakGuard:
    """Accent/case-insensitive phrase matcher. In a term, `*` stands for 0-3 arbitrary words
    ("débat * macron" matches "débat entre Emmanuel Macron")."""

    def __init__(self, terms: Iterable[str] | None = None) -> None:
        self.rules: list[tuple[str, re.Pattern[str], bool]] = []
        for t in terms or []:
            words = [w for w in (normalize(p) if p != "*" else "*" for p in t.split()) if w]
            if not words:
                continue
            rx = ""
            for w in words:
                rx += r"(?: \S+){0,3}" if w == "*" else ((" " if rx else "") + re.escape(w))
            self.rules.append((t, re.compile(rf"(?<!\S){rx}(?!\S)"), normalize(t) in WEAK_LEAK_TERMS))

    def __bool__(self) -> bool:
        return bool(self.rules)

    def find(self, text: str, official: bool = False) -> str | None:
        """Return the first matching term. Official pages are only checked against strong terms."""
        if not self.rules:
            return None
        norm = _LEAK_EXEMPT.sub(" ", normalize(text))
        for term, rx, weak in self.rules:
            if weak and official:
                continue
            if rx.search(norm):
                return term
        return None


# --------------------------------------------------------------------------------------
# Small infra: request spacing, atomic cache, SSRF guard


class Spacer:
    """Request spacing without holding a lock during the request: callers reserve a start slot."""

    def __init__(self) -> None:
        self._next = 0.0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def reserve(self, interval: float, max_wait: float = float("inf")) -> float | None:
        """Return how long to sleep before sending, or None (cooling down / queue too long)."""
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                return None
            start = max(now, self._next)
            if start - now > max_wait:
                return None
            self._next = start + interval
            return start - now

    def cooldown(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def reset(self) -> None:
        with self._lock:
            self._next = self._cooldown_until = 0.0


def atomic_write(path: Path, text: str) -> None:
    """Write via a unique temp file + rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> Any | None:
    """Cached JSON or None when missing/corrupt (a corrupt entry is a cache miss)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class UnsafeUrl(ValueError):
    pass


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (not ip.is_global) or ip.is_multicast or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def check_url_syntax(url: str) -> tuple[str, int]:
    """http(s) only, with a host; IP-literal hosts must be public. Returns (host, port)."""
    try:
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeUrl(f"URL invalide : {url[:100]}") from e
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeUrl(f"URL refusée (seuls http/https sont acceptés) : {url[:100]}")
    if p.username or p.password:
        raise UnsafeUrl("URL refusée (identifiants dans l'URL)")
    host = p.hostname
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        ip = None
    if ip is not None and _blocked_ip(ip):
        raise UnsafeUrl(f"URL refusée (adresse privée ou locale) : {host}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeUrl(f"URL refusée (hôte local) : {host}")
    return host, port


def _getaddrinfo(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


_resolve: Callable[[str, int], list[str]] = _getaddrinfo  # tests swap this


async def assert_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and hosts resolving to private/loopback/link-local/reserved addresses."""
    host, port = check_url_syntax(url)
    try:
        ipaddress.ip_address(host.split("%")[0])
        return  # IP literal, already checked
    except ValueError:
        pass
    try:
        addrs = await asyncio.to_thread(_resolve, host, port)
    except OSError as e:
        raise UnsafeUrl(f"hôte introuvable : {host}") from e
    if not addrs:
        raise UnsafeUrl(f"hôte introuvable : {host}")
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError as e:
            raise UnsafeUrl(f"adresse invalide pour {host}") from e
        if _blocked_ip(ip):
            raise UnsafeUrl(f"URL refusée ({host} pointe vers une adresse privée ou locale)")


# --------------------------------------------------------------------------------------
# Brave

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_BRAVE = Spacer()
_TAG = re.compile(r"<[^>]+>")
_BRAVE_DATE = re.compile(r"^\s*([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,3} (?:minute|hour|day|week|month|year)s? ago)\s*[-–—]\s*")


def parse_brave(html_text: str) -> list[dict[str, Any]]:
    import html as _html

    out: list[dict[str, Any]] = []
    for block in html_text.split('<div class="snippet ')[1:]:
        if 'data-type="web"' not in block[:300]:
            continue
        m = re.search(r'<a href="(https?://[^"]+)"', block)
        if not m:
            continue
        t = re.search(r'class="title[^"]*"[^>]*title="([^"]*)"', block)
        c = re.search(r'class="content[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        content = _html.unescape(_TAG.sub("", c.group(1))).strip() if c else ""
        content = re.sub(r"\s+", " ", content)
        date = ""
        dm = _BRAVE_DATE.match(content)
        if dm:
            date = dm.group(1)
        out.append({"title": _html.unescape(t.group(1)) if t else "", "link": _html.unescape(m.group(1)), "content": content, "publish_date": date})
    return out


class McpClient:
    """Minimal streamable-HTTP MCP client (initialize + tools/call)."""

    def __init__(self, server: str, api_key: str | None = None, timeout: float = 60.0) -> None:
        self.url = f"{config.ZAI_MCP_URL}/{server}/mcp"
        self.api_key = api_key or config.ZAI_API_KEY
        self.timeout = timeout
        self._sid: str | None = None
        self._lock = asyncio.Lock()
        self._id = 0

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._sid:
            h["Mcp-Session-Id"] = self._sid
        return h

    @staticmethod
    def _parse(body: str) -> dict[str, Any]:
        body = body.strip()
        if body.startswith("{"):
            return json.loads(body)
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError(f"unexpected MCP response: {body[:200]}")

    async def _init(self, client: httpx.AsyncClient) -> None:
        self._sid = None
        r = await client.post(
            self.url,
            headers=self._headers(),
            json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "sic", "version": "0.1"}},
            },
        )
        r.raise_for_status()
        self._sid = r.headers.get("mcp-session-id")
        await client.post(self.url, headers=self._headers(), json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call(self, tool: str, args: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(3):
                async with self._lock:
                    if not self._sid:
                        await self._init(client)
                    self._id += 1
                    rid = self._id
                try:
                    r = await client.post(
                        self.url,
                        headers=self._headers(),
                        json={"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": tool, "arguments": args}},
                    )
                    if r.status_code in (400, 404):  # session expired
                        self._sid = None
                        continue
                    r.raise_for_status()
                    data = self._parse(r.text)
                    if "error" in data:
                        raise RuntimeError(str(data["error"]))
                    res = data["result"]
                    text = "".join(c.get("text", "") for c in res.get("content", []))
                    if res.get("isError") or '"code":"1234"' in text[:300]:
                        raise ValueError(text[:300])
                    return text
                except (httpx.HTTPError, ValueError, RuntimeError) as e:
                    if attempt == 2:
                        raise RuntimeError(f"MCP {tool} failed: {e}") from e
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"MCP {tool} failed")


def _unwrap_json(text: Any) -> Any:
    data = text
    for _ in range(2):  # payloads are often a JSON string containing JSON
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                break
    return data


class WebConnector:
    name = "web"

    def __init__(self, leak_terms: list[str] | None = None) -> None:
        self.search_client = McpClient("web_search_prime")
        self.reader_client = McpClient("web_reader")
        self.leak = LeakGuard(leak_terms)
        self.cache_dir = config.CACHE / "web"

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="web_search",
                description=(
                    "Recherche web. À utiliser quand aucune source structurée (INSEE, Eurostat, Assemblée) ne couvre la "
                    "question : lois, faits historiques, déclarations, chiffres de ministères, rapports (Cour des comptes, "
                    "COR, RTE...), fact-checks déjà publiés. Requête courte (< 70 caractères), en français, précise "
                    "(chiffre + période). `domains` restreint la recherche (ex. ['insee.fr','vie-publique.fr'])."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="web_read",
                description="Lire le contenu d'une page (URL issue d'une recherche) pour vérifier un chiffre précis.",
                parameters={"type": "object", "properties": {"url": {"type": "string"}, "focus": {"type": "string", "description": "ce que tu cherches dans la page"}}, "required": ["url"]},
            ),
        ]

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        try:
            if tool == "web_search":
                return await self.search(args.get("query", ""), args.get("domains") or [], ctx)
            if tool == "web_read":
                return await self.read(args.get("url", ""), args.get("focus"), ctx)
        except Exception as e:  # tool errors go back to the model as text
            return ToolResult(content=f"Erreur {tool}: {e}")
        return ToolResult(content=f"Outil inconnu: {tool}")

    # -- cache

    def _cache_get(self, kind: str, key: dict[str, Any]) -> tuple[Any | None, Path]:
        h = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        p = self.cache_dir / kind / f"{h}.json"
        data = read_json(p)
        ok = isinstance(data, list) if kind == "search" else isinstance(data, dict) and isinstance(data.get("content"), str)
        return (data if ok else None), p

    @staticmethod
    def _cache_put(path: Path, data: Any) -> None:
        atomic_write(path, json.dumps(data, ensure_ascii=False))

    # -- providers

    async def _brave_search(self, query: str) -> list[dict[str, Any]]:
        """Brave Search: official API when BRAVE_API_KEY is set, else the HTML page (dev fallback,
        rate-limited: requests are spaced — without holding a lock — and we back off on 429)."""
        key = {"brave": query}
        cached, path = self._cache_get("search", key)
        if cached is not None:
            return cached
        api_key = os.environ.get("BRAVE_API_KEY")
        wait = _BRAVE.reserve(0.2 if api_key else 2.0, BRAVE_MAX_WAIT)
        if wait is None:  # cooling down after a 429, or too many searches queued: skip Brave
            return []
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, headers={"User-Agent": _UA, "Accept-Language": "fr-FR,fr;q=0.9"}) as c:
            if api_key:
                r = await c.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "country": "FR", "search_lang": "fr", "count": 10},
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                )
            else:
                r = await c.get("https://search.brave.com/search", params={"q": query, "source": "web"})
        if r.status_code == 429:
            _BRAVE.cooldown(5 if api_key else 90)
            return []
        if r.status_code != 200:
            return []
        if api_key:
            results = [
                {"title": x.get("title", ""), "link": x.get("url", ""), "content": _TAG.sub("", x.get("description", "")),
                 "publish_date": (x.get("page_age") or "")[:10]}
                for x in (r.json().get("web", {}).get("results") or [])
            ]
        else:
            results = parse_brave(r.text)
        self._cache_put(path, results)
        return results

    async def _raw_search(self, query: str, domain: str | None) -> list[dict[str, Any]]:
        key = {"q": query, "d": domain}
        cached, path = self._cache_get("search", key)
        if cached is not None:
            return cached
        args: dict[str, Any] = {"search_query": query[:70], "location": "us"}
        if domain:
            args["search_domain_filter"] = domain
        data = _unwrap_json(await self.search_client.call("web_search_prime", args))
        results = data if isinstance(data, list) else []
        self._cache_put(path, results)
        return results

    def _leaks(self, text: str, official: bool = False) -> bool:
        return self.leak.find(text, official) is not None

    async def search(self, query: str, domains: list[str], ctx: VerifyContext) -> ToolResult:
        # The engine's domain filter returns homepage links only, so we hint domains in the query
        # instead and re-rank: results from requested domains first.
        wanted = [d.lower().removeprefix("www.") for d in domains[:3]]
        if wanted:
            calls: list[Awaitable[list[dict[str, Any]]]] = [self._brave_search(f"{query} site:{d}") for d in wanted[:BRAVE_MAX_QUERIES]]
            calls.append(self._raw_search(f"{query} {wanted[0].split('.')[0]}", None))
        else:
            calls = [self._brave_search(query), self._raw_search(query, None)]
        # Each provider gets its own timeout: a slow/queued one must not lose the others' results.
        batches = await asyncio.gather(*(asyncio.wait_for(c, PROVIDER_TIMEOUT) for c in calls), return_exceptions=True)
        failed = sum(isinstance(b, BaseException) for b in batches)
        seen: set[str] = set()
        items: list[tuple[int, dict[str, Any], PubDate | None, int, str]] = []
        drops = {"after": 0, "undated": 0, "leak": 0}
        for b in batches:
            if isinstance(b, BaseException):
                continue
            for r in b:
                url = r.get("link") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                content = r.get("content") or ""
                cls = classify(url)
                pd = publication_date(r.get("publish_date"), url, content)
                if ctx.as_of:
                    if self._leaks(f"{r.get('title', '')} {content} {urlsplit(url).path}", official=cls == "trusted"):
                        drops["leak"] += 1
                        continue
                    status = strict_status(pd, ctx.as_of)
                    if status == "after" or any(d > ctx.as_of for d in _iso_dates(content)):
                        drops["after"] += 1
                        continue
                    if status == "unknown" and cls != "trusted":
                        drops["undated"] += 1
                        continue
                    if status == "unknown":
                        pd = None
                elif pd and pd.origin == "text":
                    pd = None  # a date mentioned in the snippet is not a publication date
                rank = {"trusted": 0, "factcheck": 1, "other": 2}[cls]
                host = urlparse(url).hostname or ""
                boost = 0 if any(host == w or host.endswith("." + w) for w in wanted) else 1
                precise = bool(urlparse(url).path.strip("/"))
                if not precise:  # homepage-only link (search engine quirk): weak citation
                    rank += 3
                items.append((rank, r, pd, boost, url))
        items.sort(key=lambda x: (x[3], x[0]))
        lines: list[str] = []
        sources: list[Source] = []
        for i, (rank, r, pd, _, url) in enumerate(items[:8]):
            host = urlparse(url).hostname or ""
            snippet = re.sub(r"\s+", " ", r.get("content") or "")[:500]
            vague = rank >= 3
            tag = ["source officielle", "fact-check", "autre"][rank % 3] + (" — lien imprécis" if vague else "")
            date_s = pd.label if pd else ("non daté" if ctx.as_of else "")
            lines.append(f"[{i + 1}] {r.get('title', '')} — {host} ({tag}{', ' + date_s if date_s else ''})\nURL: {url}\n{snippet}")
            excerpt = ("(lien imprécis) " + snippet)[:300] if vague else snippet[:300]
            sources.append(
                Source(kind=source_kind(url), title=r.get("title", "")[:200], url=url, publisher=host.removeprefix("www."),
                       date=pd.label if pd else None, excerpt=excerpt)
            )
        head = f"Résultats pour « {query} »"
        if ctx.as_of:
            n = sum(drops.values())
            detail = ", ".join(f"{v} {label}" for v, label in (
                (drops["after"], "postérieur(s)"), (drops["undated"], "non daté(s) hors sources officielles"),
                (drops["leak"], "sur le débat lui-même"),
            ) if v)
            head += f" (antérieurs au {ctx.as_of.isoformat()} ; {n} résultat(s) écarté(s){' : ' + detail if detail else ''})"
        if failed:
            head += f" [{failed} moteur(s) sans réponse à temps]"
        if not lines:
            return ToolResult(content=head + " : aucun résultat exploitable.")
        return ToolResult(content=head + "\n\n" + "\n\n".join(lines), sources=sources)

    async def read(self, url: str, focus: str | None, ctx: VerifyContext) -> ToolResult:
        try:
            check_url_syntax(url)
        except UnsafeUrl as e:
            return ToolResult(content=str(e))
        cached, path = self._cache_get("read", {"u": url})
        if cached is None:
            try:
                await assert_public_url(url)
            except UnsafeUrl as e:
                return ToolResult(content=str(e))
            try:
                text = await self.reader_client.call("webReader", {"url": url, "return_format": "text", "retain_images": False, "timeout": 25})
            except RuntimeError:
                text = await _direct_fetch(url)
            data = _unwrap_json(text)
            if isinstance(data, dict):
                cached = {"title": data.get("title", ""), "content": data.get("content") or data.get("text") or "",
                          "date": data.get("published_time") or data.get("publishedTime") or data.get("date") or ""}
            else:
                cached = {"title": "", "content": str(data), "date": ""}
            self._cache_put(path, cached)
        content = re.sub(r"\n{3,}", "\n\n", str(cached.get("content") or ""))
        pd = publication_date(str(cached.get("date") or ""), url, content[:2000])
        official = classify(url) == "trusted"
        warning = ""
        if ctx.as_of:
            status = strict_status(pd, ctx.as_of)
            if status == "after" and pd:
                return ToolResult(content=f"Page publiée le {pd.label} (après le {ctx.as_of}) : ignorée en mode direct.")
            if self._leaks(f"{cached.get('title', '')}\n{content[:3000]}\n{urlsplit(url).path}", official=official):
                return ToolResult(content="Page écartée : elle commente le débat lui-même (mode direct).")
            if status == "unknown":
                pd = None
                if not official:
                    warning = (
                        f"ATTENTION : date de publication inconnue. N'utilise aucune information postérieure au "
                        f"{ctx.as_of.isoformat()} (chiffres, événements, réactions) ; dans le doute, ne t'appuie pas sur cette page.\n"
                    )
        elif pd and pd.origin == "text":
            pd = None
        excerpt = _focus_excerpt(content, focus, 3500)
        host = urlparse(url).hostname or ""
        src = Source(kind=source_kind(url), title=(cached.get("title") or host)[:200], url=url, publisher=host.removeprefix("www."),
                     date=pd.label if pd else None, excerpt=None)
        return ToolResult(content=f"{warning}{cached.get('title', '')}\n{url}\n\n{excerpt}", sources=[src])


async def _direct_fetch(url: str) -> str:
    """Fallback reader: plain GET + crude HTML-to-text. Redirects are followed by hand so that
    every hop is checked against private/local addresses."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (sic.vote fact-checker)"}) as c:
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(url)
            async with c.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(str(r.url), r.headers["location"])
                    continue
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) >= MAX_FETCH_BYTES:
                        break
                html = buf.decode(r.encoding or "utf-8", errors="replace")
                break
        else:
            raise RuntimeError(f"trop de redirections (> {MAX_REDIRECTS})")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    body = re.sub(r"(?is)<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    import html as _html

    body = _html.unescape(re.sub(r"[ \t]+", " ", body))
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    meta = re.search(r'<meta[^>]+(?:property|name)="(?:article:published_time|date|datePublished)"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    return json.dumps({"title": _html.unescape(title.group(1).strip()) if title else "", "content": body.strip(),
                       "published_time": meta.group(1) if meta else ""})


def _focus_excerpt(text: str, focus: str | None, limit: int) -> str:
    if len(text) <= limit or not focus:
        return text[:limit]
    # Keep paragraphs that share the most words with the focus.
    words = {w for w in re.findall(r"\w{4,}", focus.lower())}
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    scored = sorted(range(len(paras)), key=lambda i: -sum(w in paras[i].lower() for w in words))
    keep = sorted(scored[:12])
    out = "\n\n".join(paras[i] for i in keep)
    return out[:limit]


def today() -> Date:
    return datetime.now(UTC).date()
