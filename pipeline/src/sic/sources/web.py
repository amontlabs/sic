"""Web search + page reading through z.ai MCP servers (web_search_prime, web_reader).

Results are filtered:
- `as_of` (strict live simulation): drop results dated after the claim date, and drop
  pages that obviously discuss the debate itself (leak guard).
- domains are tagged trusted / fact-checker / other; the verifier prefers trusted ones.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from datetime import date as Date
from typing import Any
from urllib.parse import urlparse

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

_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_EN = re.compile(r"^\s*([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")
_DATE_ANY = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


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


def parse_date(text: str) -> Date | None:
    m = _DATE_EN.match(text or "")
    if m and m.group(1).lower() in _MONTHS:
        try:
            return Date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    m = _DATE_ANY.search(text or "")
    if m:
        try:
            return Date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_BRAVE_LOCK = asyncio.Lock()
_BRAVE_COOLDOWN_UNTIL = 0.0
_TAG = re.compile(r"<[^>]+>")
_BRAVE_DATE = re.compile(r"^\s*([A-Z][a-z]+ \d{1,2}, \d{4})\s*[-–]\s*")


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


class WebConnector:
    name = "web"

    def __init__(self, leak_terms: list[str] | None = None) -> None:
        self.search_client = McpClient("web_search_prime")
        self.reader_client = McpClient("web_reader")
        self.leak_terms = [t.lower() for t in (leak_terms or [])]
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

    def _cache_get(self, kind: str, key: dict[str, Any]) -> Any | None:
        h = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        p = self.cache_dir / kind / f"{h}.json"
        return (json.loads(p.read_text()), p) if p.exists() else (None, p)

    async def _brave_search(self, query: str) -> list[dict[str, Any]]:
        """Brave Search: official API when BRAVE_API_KEY is set, else the HTML page (dev fallback,
        rate-limited: we space requests and back off on 429)."""
        global _BRAVE_COOLDOWN_UNTIL
        key = {"brave": query}
        cached, path = self._cache_get("search", key)
        if cached is not None:
            return cached
        api_key = os.environ.get("BRAVE_API_KEY")
        async with _BRAVE_LOCK:
            if not api_key and time.monotonic() < _BRAVE_COOLDOWN_UNTIL:
                return []
            await asyncio.sleep(0.2 if api_key else 2.0)
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA, "Accept-Language": "fr-FR,fr;q=0.9"}) as c:
                if api_key:
                    r = await c.get(
                        "https://api.search.brave.com/res/v1/web/search",
                        params={"q": query, "country": "FR", "search_lang": "fr", "count": 10},
                        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                    )
                else:
                    r = await c.get("https://search.brave.com/search", params={"q": query, "source": "web"})
        if r.status_code == 429:
            _BRAVE_COOLDOWN_UNTIL = time.monotonic() + 90
            return []
        if r.status_code != 200:
            return []
        if api_key:
            results = [
                {"title": x.get("title", ""), "link": x.get("url", ""), "content": _TAG.sub("", x.get("description", "")),
                 "publish_date": x.get("page_age", "")[:10]}
                for x in (r.json().get("web", {}).get("results") or [])
            ]
        else:
            results = parse_brave(r.text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, ensure_ascii=False))
        return results

    async def _raw_search(self, query: str, domain: str | None) -> list[dict[str, Any]]:
        key = {"q": query, "d": domain}
        cached, path = self._cache_get("search", key)
        if cached is not None:
            return cached
        args: dict[str, Any] = {"search_query": query[:70], "location": "us"}
        if domain:
            args["search_domain_filter"] = domain
        text = await self.search_client.call("web_search_prime", args)
        data: Any = text
        for _ in range(2):  # payload is a JSON string containing JSON
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    break
        results = data if isinstance(data, list) else []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, ensure_ascii=False))
        return results

    def _leaks(self, text: str) -> bool:
        low = text.lower()
        return any(t in low for t in self.leak_terms)

    async def search(self, query: str, domains: list[str], ctx: VerifyContext) -> ToolResult:
        # The engine's domain filter returns homepage links only, so we hint domains in the query
        # instead and re-rank: results from requested domains first.
        wanted = [d.lower().removeprefix("www.") for d in domains[:3]]
        if wanted:
            tasks = [self._brave_search(f"{query} site:{d}") for d in wanted]
            tasks += [self._raw_search(f"{query} {d.split('.')[0]}", None) for d in wanted[:1]]
        else:
            tasks = [self._brave_search(query), self._raw_search(query, None)]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        seen: set[str] = set()
        items: list[tuple[int, dict[str, Any], Date | None]] = []
        dropped = 0
        for b in batches:
            if isinstance(b, BaseException):
                continue
            for r in b:
                url = r.get("link") or ""
                if not url or url in seen:
                    continue
                precise = bool(urlparse(url).path.strip("/"))
                seen.add(url)
                content = r.get("content") or ""
                d = parse_date(content) or parse_date(r.get("publish_date") or "")
                if ctx.as_of and ((d and d > ctx.as_of) or self._leaks(r.get("title", "") + " " + content)):
                    dropped += 1
                    continue
                rank = {"trusted": 0, "factcheck": 1, "other": 2}[classify(url)]
                host = urlparse(url).hostname or ""
                boost = 0 if any(host == w or host.endswith("." + w) for w in wanted) else 1
                if not precise:  # homepage-only link (search engine quirk): weak citation
                    rank += 3
                items.append((rank, r, d, boost))
        items.sort(key=lambda x: (x[3], x[0]))
        lines: list[str] = []
        sources: list[Source] = []
        for i, (rank, r, d, _) in enumerate(items[:8]):
            url = r.get("link", "")
            host = urlparse(url).hostname or ""
            snippet = re.sub(r"\s+", " ", r.get("content") or "")[:500]
            tag = ["source officielle", "fact-check", "autre"][rank % 3] + (" — lien imprécis" if rank >= 3 else "")
            lines.append(f"[{i + 1}] {r.get('title', '')} — {host} ({tag}{', ' + d.isoformat() if d else ''})\nURL: {url}\n{snippet}")
            sources.append(
                Source(kind=source_kind(url), title=r.get("title", "")[:200], url=url, publisher=host.removeprefix("www."),
                       date=d.isoformat() if d else None, excerpt=snippet[:300])
            )
        head = f"Résultats pour « {query} »"
        if ctx.as_of:
            head += f" (antérieurs au {ctx.as_of.isoformat()} ; {dropped} résultat(s) écarté(s))"
        if not lines:
            return ToolResult(content=head + " : aucun résultat exploitable.")
        return ToolResult(content=head + "\n\n" + "\n\n".join(lines), sources=sources)

    async def read(self, url: str, focus: str | None, ctx: VerifyContext) -> ToolResult:
        if not url.startswith("http"):
            return ToolResult(content="URL invalide")
        cached, path = self._cache_get("read", {"u": url})
        if cached is None:
            try:
                text = await self.reader_client.call("webReader", {"url": url, "return_format": "text", "retain_images": False, "timeout": 25})
            except RuntimeError:
                text = await _direct_fetch(url)
            data: Any = text
            for _ in range(2):
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        break
            if isinstance(data, dict):
                cached = {"title": data.get("title", ""), "content": data.get("content") or data.get("text") or "", "date": data.get("published_time") or ""}
            else:
                cached = {"title": "", "content": str(data), "date": ""}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached, ensure_ascii=False))
        content = re.sub(r"\n{3,}", "\n\n", cached["content"])
        d = parse_date(cached.get("date") or "")
        if ctx.as_of and d and d > ctx.as_of:
            return ToolResult(content=f"Page publiée le {d} (après le {ctx.as_of}) : ignorée en mode direct.")
        if ctx.as_of and self._leaks(content[:3000]):
            return ToolResult(content="Page écartée : elle commente le débat lui-même (mode direct).")
        excerpt = _focus_excerpt(content, focus, 3500)
        host = urlparse(url).hostname or ""
        src = Source(kind=source_kind(url), title=(cached.get("title") or host)[:200], url=url, publisher=host.removeprefix("www."),
                     date=d.isoformat() if d else None, excerpt=None)
        return ToolResult(content=f"{cached.get('title', '')}\n{url}\n\n{excerpt}", sources=[src])


async def _direct_fetch(url: str) -> str:
    """Fallback reader: plain GET + crude HTML-to-text."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (sic.vote fact-checker)"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        html = r.text
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    body = re.sub(r"(?is)<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    import html as _html

    body = _html.unescape(re.sub(r"[ \t]+", " ", body))
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return json.dumps({"title": _html.unescape(title.group(1).strip()) if title else "", "content": body.strip()})


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
