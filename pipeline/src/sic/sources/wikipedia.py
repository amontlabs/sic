"""French Wikipedia through the public MediaWiki API, read *as it was* on a given date.

- `wikipedia_search`: full-text search (titles + snippets).
- `wikipedia_read`: plain-text excerpt of an article. In strict mode (`ctx.as_of`), we read the last
  revision saved before that day (`prop=revisions&rvstart=<as_of>T00:00:00Z&rvdir=older`), render
  that exact revision (`action=parse&oldid=…`) and cite its permalink; paragraphs that mention the
  debate itself are dropped. Without `as_of`, the current revision is used.

  Why the start of the day rather than its end: debates air in the evening and Wikipedia is edited
  live (e.g. "Élection présidentielle française de 2022" rev 193022190 was saved at 2022-04-20T21:14Z,
  during the debate), so an end-of-day snapshot can already contain the debate.

Politeness: descriptive User-Agent, ≤ ~5 requests/s process-wide, retries on 429/5xx (honouring
Retry-After), disk cache (revision-pinned responses never expire; search/current revision: 1 day).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date as Date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from sic import config
from sic.models import Source
from sic.sources.base import ToolResult, ToolSpec, VerifyContext
from sic.sources.web import LeakGuard, Spacer, atomic_write, normalize, read_json, today

API = "https://fr.wikipedia.org/w/api.php"
USER_AGENT = "SicFactCheck/0.1 (contact: dev@sic.vote)"
CACHE_DIR = config.CACHE / "wikipedia"
REVISION_CUTOFF = "T00:00:00Z"  # appended to as_of: last revision saved before the debate day
TTL = 24 * 3600.0  # search results and "current revision" lookups
MAX_CHARS = 3500
_SPACER = Spacer()  # process-wide: all connectors share the request budget

_MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


class WikipediaError(RuntimeError):
    pass


def date_fr(d: Date) -> str:
    return f"{'1er' if d.day == 1 else d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


class WikiApi:
    """GET client for the MediaWiki API: cache, request spacing, retries."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        min_interval: float = 0.2,
        retries: int = 3,
        backoff: float = 1.0,
        timeout: float = 20.0,
        ttl: float = TTL,
    ) -> None:
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.ttl = ttl

    def _path(self, params: dict[str, Any]) -> Path:
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    async def get(self, params: dict[str, Any], *, immutable: bool = False) -> dict[str, Any]:
        params = {**{k: str(v) for k, v in params.items()}, "format": "json", "formatversion": "2"}
        path = self._path(params)
        cached = read_json(path)
        if isinstance(cached, dict):
            try:
                fresh = immutable or time.time() - path.stat().st_mtime < self.ttl
            except OSError:
                fresh = False
            if fresh:
                return cached
        last = ""
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
            for attempt in range(self.retries + 1):
                wait = _SPACER.reserve(self.min_interval)
                if wait:
                    await asyncio.sleep(wait)
                try:
                    r = await client.get(API, params=params)
                except httpx.HTTPError as e:
                    last = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(min(2**attempt, 10) * self.backoff)
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    ra = r.headers.get("retry-after", "")
                    delay = float(ra) if ra.isdigit() else 2 ** (attempt + 1)
                    await asyncio.sleep(min(delay, 30) * self.backoff)
                    continue
                if r.status_code != 200:
                    raise WikipediaError(f"HTTP {r.status_code}")
                try:
                    data = r.json()
                except ValueError as e:
                    raise WikipediaError("réponse illisible de l'API Wikipédia") from e
                if not isinstance(data, dict):
                    raise WikipediaError("réponse inattendue de l'API Wikipédia")
                if "error" in data:
                    err = data["error"] or {}
                    raise WikipediaError(f"{err.get('code', 'erreur')} : {err.get('info', '')}".strip())
                atomic_write(path, json.dumps(data, ensure_ascii=False))
                return data
        if isinstance(cached, dict):  # network trouble: a stale entry beats nothing
            return cached
        raise WikipediaError(f"API Wikipédia indisponible ({last})")


# --------------------------------------------------------------------------------------
# HTML -> text blocks

_SKIP_TAGS = {"style", "script", "noscript", "figure", "math", "audio", "video", "map", "link", "meta"}
_SKIP_CLASS_PREFIXES = (
    "reference", "mw-references", "reflist", "navbox", "infobox", "bandeau", "metadata", "hatnote", "thumb",
    "mw-editsection", "noprint", "printfooter", "catlinks", "toc", "homonymie", "mw-cite-backlink", "gallery",
    "autres-projets", "mwe-math", "error", "mw-empty-elt", "sister", "navigation-only", "portail", "boilerplate",
)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BLOCKS = {"p", "li", "dd", "dt", "blockquote", "caption", "h2", "h3", "h4", "h5", "h6"}
_STOP_SECTIONS = {"notes et references", "references", "notes", "voir aussi", "liens externes", "bibliographie", "articles connexes", "annexes"}
_CITE = re.compile(r"\[\s*(?:\d+|[a-z]{1,2}|note \d+|n \d+|réf\. (?:nécessaire|souhaitée)|[^\]]{0,20}\?)\s*\]")


@dataclass
class Block:
    section: str  # "Historique > Entrée en vigueur" ("" for the lead)
    text: str
    kind: str = "p"  # "p" (paragraph / list item) or "table"


class _WikiText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.skip_tag: str | None = None
        self.skip_depth = 0
        self.heading: str | None = None
        self.sections: dict[int, str] = {}
        self.table_depth = 0
        self.rows: list[str] = []
        self.stopped = False

    @property
    def section(self) -> str:
        return " > ".join(self.sections[k] for k in sorted(self.sections))

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", _CITE.sub("", "".join(self.buf))).strip()
        self.buf = []
        if not text or not re.search(r"\w", text):
            return
        if self.table_depth:
            self.rows.append(text)
        else:
            self.blocks.append(Block(self.section, text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.stopped:
            return
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        a = dict(attrs)
        classes = (a.get("class") or "").lower().split()
        style = (a.get("style") or "").replace(" ", "").lower()
        skip = (
            tag in _SKIP_TAGS
            or any(c.startswith(_SKIP_CLASS_PREFIXES) for c in classes)
            or a.get("role") == "navigation"
            or "display:none" in style
            or (tag == "table" and "wikitable" not in classes)
        )
        if skip and tag not in _VOID:
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag == "table":
            self._flush()
            self.table_depth += 1
            if self.table_depth == 1:
                self.rows = []
        elif tag == "tr":
            self._flush()
        elif tag in ("td", "th"):
            if self.buf and "".join(self.buf).strip():
                self.buf.append(" | ")
        elif tag in _BLOCKS:
            self._flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.heading = tag
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.stopped:
            return
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return
        if self.heading and tag == self.heading:
            title = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            self.buf, level, self.heading = [], int(tag[1:]), None
            if level == 2 and normalize(title) in _STOP_SECTIONS:
                self.stopped = True
                return
            self.sections = {k: v for k, v in self.sections.items() if k < level}
            if title:
                self.sections[level] = title
            return
        if tag == "tr":
            self._flush()
        elif tag == "table" and self.table_depth:
            self._flush()
            self.table_depth -= 1
            if self.table_depth == 0 and self.rows:
                self.blocks.append(Block(self.section, "\n".join(self.rows)[:1500], "table"))
                self.rows = []
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.stopped and not self.skip_tag:
            self.buf.append(data)


def html_to_blocks(html: str) -> list[Block]:
    p = _WikiText()
    p.feed(html)
    p.close()
    if not p.stopped:
        p._flush()
    return p.blocks


_STOPWORDS = {"dans", "pour", "avec", "sans", "plus", "moins", "entre", "depuis", "selon", "quel", "quelle", "quels", "quelles",
              "sont", "etait", "cette", "elle", "leur", "leurs", "comme", "aussi", "france", "francais", "francaise"}


def _focus_words(focus: str | None) -> list[str]:
    words = [w for w in normalize(focus or "").split() if (len(w) >= 4 or w.isdigit()) and w not in _STOPWORDS]
    return list(dict.fromkeys(words))


def focus_excerpt(blocks: list[Block], focus: str | None, limit: int = MAX_CHARS) -> str:
    """Lead paragraph + the blocks sharing the most words with `focus`, in document order, with
    their section titles; at most `limit` characters."""
    if not blocks:
        return ""
    words = _focus_words(focus)

    def score(b: Block) -> float:
        norm = " " + normalize(b.section + " " + b.text)
        return sum((" " + w) in norm for w in words) + 0.5 * sum(w in normalize(b.section) for w in words)

    if words:
        scores = [score(b) for b in blocks]
        order = sorted(range(len(blocks)), key=lambda i: (-scores[i], i))
        chosen = [0] + [i for i in order if scores[i] > 0 and i != 0]
        if len(chosen) == 1:  # nothing matched: fall back to the beginning of the article
            chosen = list(range(len(blocks)))
    else:
        chosen = list(range(len(blocks)))
    keep: list[int] = []
    used = 0
    for i in chosen:
        cost = len(blocks[i].text) + len(blocks[i].section) + 8
        if used + cost > limit:
            if not keep:
                keep.append(i)
            continue
        keep.append(i)
        used += cost
    out: list[str] = []
    section = None
    for i in sorted(keep):
        b = blocks[i]
        if b.section and b.section != section:
            out.append(f"## {b.section}")
        section = b.section
        out.append(b.text)
    return "\n\n".join(out)[:limit]


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    if t.startswith(("http://", "https://")):
        parts = urlsplit(t)
        t = unquote(parts.path.rsplit("/wiki/", 1)[-1]) if "/wiki/" in parts.path else t
    return t.split("#", 1)[0].replace("_", " ").strip()


_YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


class WikipediaConnector:
    name = "wikipedia"

    def __init__(self, leak_terms: list[str] | None = None, api: WikiApi | None = None) -> None:
        self.api = api or WikiApi()
        self.leak = LeakGuard(leak_terms)

    def tools(self) -> list[ToolSpec]:
        common = (
            "Utile pour les faits historiques, les dates, les institutions, l'histoire des lois et « qui a fait quoi » "
            "(gouvernements, réformes, élections). Pas pour les statistiques récentes (utilise INSEE / Eurostat) ; "
            "recoupe toujours les chiffres avec une source officielle quand c'est possible."
        )
        return [
            ToolSpec(
                name="wikipedia_search",
                description=f"Recherche dans Wikipédia en français (titres + extraits). {common} "
                "Lis ensuite l'article avec wikipedia_read (c'est lui qui fournit la source citable).",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "mots-clés en français"},
                        "limit": {"type": "integer", "description": "nombre de résultats (1-10, défaut 5)"},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="wikipedia_read",
                description=f"Lire un article de Wikipédia en français ; en mode direct, dans sa version antérieure au débat "
                f"(lien permanent vers cette version). {common}",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "titre exact de l'article (ex. « Taxe carbone en France »)"},
                        "focus": {"type": "string", "description": "ce que tu cherches dans l'article (mots-clés)"},
                    },
                    "required": ["title"],
                },
            ),
        ]

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        try:
            if tool == "wikipedia_search":
                return await self.search(str(args.get("query") or ""), args.get("limit"), ctx)
            if tool == "wikipedia_read":
                return await self.read(str(args.get("title") or ""), args.get("focus"), ctx)
        except Exception as e:  # noqa: BLE001 — tool errors go back to the model as text
            return ToolResult(content=f"Erreur {tool}: {e}")
        return ToolResult(content=f"Outil inconnu: {tool}")

    async def search(self, query: str, limit: Any, ctx: VerifyContext) -> ToolResult:
        query = query.strip()
        if not query:
            return ToolResult(content="Requête vide.")
        try:
            n = max(1, min(10, int(limit or 5)))
        except (TypeError, ValueError):
            n = 5
        data = await self.api.get(
            {"action": "query", "list": "search", "srsearch": query, "srlimit": n, "srprop": "snippet|timestamp", "srnamespace": 0}
        )
        hits = (data.get("query") or {}).get("search") or []
        lines: list[str] = []
        dropped = 0
        for h in hits:
            title = str(h.get("title") or "")
            if not title:
                continue
            if ctx.as_of and (self.leak.find(title) or any(int(y) > ctx.as_of.year for y in _YEAR.findall(title))):
                dropped += 1
                continue
            if ctx.as_of:  # snippets come from today's version of the article: never show them in strict mode
                lines.append(f"[{len(lines) + 1}] {title}")
            else:
                snippet = re.sub(r"\s+", " ", _strip_tags(str(h.get("snippet") or ""))).strip()
                lines.append(f"[{len(lines) + 1}] {title} — {snippet}")
        head = f"Wikipédia, recherche « {query} »"
        if ctx.as_of:
            head += (
                f" (mode direct : extraits masqués car issus de la version actuelle ; wikipedia_read lit la version antérieure au "
                f"{date_fr(ctx.as_of)}{f' ; {dropped} titre(s) écarté(s)' if dropped else ''})"
            )
        if not lines:
            return ToolResult(content=head + " : aucun résultat.")
        return ToolResult(content=head + "\n" + "\n".join(lines))

    async def read(self, title: str, focus: str | None, ctx: VerifyContext) -> ToolResult:
        title = _clean_title(title)
        if not title:
            return ToolResult(content="Titre d'article manquant.")
        params: dict[str, Any] = {
            "action": "query", "prop": "revisions", "titles": title, "rvlimit": 1, "rvprop": "ids|timestamp", "redirects": 1,
        }
        if ctx.as_of:
            params |= {"rvstart": f"{ctx.as_of.isoformat()}{REVISION_CUTOFF}", "rvdir": "older"}
        data = await self.api.get(params, immutable=bool(ctx.as_of and ctx.as_of < today()))
        q = data.get("query") or {}
        pages = q.get("pages") or []
        page = pages[0] if pages else {}
        if not page or page.get("missing") or page.get("invalid"):
            return ToolResult(content=f"Article « {title} » introuvable sur Wikipédia (fr). Cherche le titre exact avec wikipedia_search.")
        page_title = str(page.get("title") or title)
        revs = page.get("revisions") or []
        if not revs:
            when = f"avant le {date_fr(ctx.as_of)}" if ctx.as_of else "(aucune révision)"
            return ToolResult(content=f"L'article « {page_title} » n'existait pas encore {when} : il ne peut pas être utilisé.")
        revid = int(revs[0]["revid"])
        ts = str(revs[0].get("timestamp") or "")
        rev_date = Date.fromisoformat(ts[:10]) if ts else None
        parsed = await self.api.get(
            {"action": "parse", "oldid": revid, "prop": "text", "disableeditsection": 1, "disablelimitreport": 1, "disabletoc": 1},
            immutable=True,
        )
        html = (parsed.get("parse") or {}).get("text") or ""
        if isinstance(html, dict):  # formatversion=1 shape
            html = html.get("*", "")
        blocks = html_to_blocks(str(html))
        removed = 0
        if ctx.as_of and self.leak:
            kept = [b for b in blocks if not self.leak.find(b.text)]
            removed = len(blocks) - len(kept)
            blocks = kept
        excerpt = focus_excerpt(blocks, focus, MAX_CHARS)
        if not excerpt:
            return ToolResult(content=f"Article « {page_title} » : aucun texte exploitable dans cette version.")
        permalink = f"https://fr.wikipedia.org/w/index.php?oldid={revid}"
        version = date_fr(rev_date) if rev_date else f"révision {revid}"
        head = f"Wikipédia — « {page_title} », version du {version} (révision {revid}"
        head += f", dernière version antérieure au {date_fr(ctx.as_of)})" if ctx.as_of else ", version actuelle)"
        redirects = q.get("redirects") or []
        if redirects:
            head += f" [redirigé depuis « {redirects[0].get('from', title)} »]"
        head += f"\nLien permanent : {permalink}"
        if removed:
            head += f"\n({removed} paragraphe(s) écarté(s) : ils évoquent le débat lui-même)"
        head += "\nSource secondaire : recoupe les chiffres avec une source officielle."
        src = Source(
            kind="web",
            publisher="Wikipédia",
            title=f"{page_title} (version du {version})"[:200],
            url=permalink,
            date=rev_date.isoformat() if rev_date else None,
            excerpt=re.sub(r"\s+", " ", excerpt)[:300],
        )
        return ToolResult(content=f"{head}\n\n{excerpt}", sources=[src])


def _strip_tags(html: str) -> str:
    import html as _html

    return _html.unescape(re.sub(r"<[^>]+>", "", html))
