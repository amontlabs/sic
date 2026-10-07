"""Verify one claim: library match first, then a tool-using LLM over official sources."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from sic import config
from sic.library import Library, LibraryEntry, entry_text
from sic.llm import LLM, parse_json
from sic.models import Claim, Source, Verdict
from sic.sources.base import Connector, ToolResult, VerifyContext

# Bump when prompts/rules change: cached verdicts from an older verifier are then re-computed.
VERIFIER_VERSION = "2026-10-07.3"

LABELS = ("exact", "plutot_exact", "imprecis", "trompeur", "faux", "inverifiable")
NEGATIVE = ("imprecis", "trompeur", "faux")

SCALE = """Échelle des verdicts :
- exact : vrai et précis (les arrondis raisonnables sont acceptés : « près de 7 % » pour 6,8 % est exact) ;
- plutot_exact : vrai sur le fond, avec une petite approximation ou une nuance mineure ;
- imprecis : en partie vrai, mais chiffre, période ou périmètre inexact ou exagéré ;
- trompeur : les faits cités peuvent être exacts mais présentés de façon à induire en erreur (contexte décisif omis) ;
- faux : contredit par des sources fiables ;
- inverifiable : pas de source fiable trouvée, ou affirmation trop vague pour être vérifiée."""

RULES = """Règles :
- Neutralité absolue : même exigence pour tous. Jamais de mot comme « mensonge » ni de jugement d'intention.
- Sois exigeant : « exact » ou « plutot_exact » seulement si une source confirme le fait ET le chiffre tel
  qu'il a été dit (même ordre de grandeur, même période, même périmètre). Vérifie aussi ce que la phrase
  laisse entendre : un chiffre juste utilisé pour suggérer une conclusion fausse est « trompeur ».
- Ne complète pas les trous avec tes connaissances : si les sources obtenues ne tranchent pas, c'est
  « inverifiable ». Une affirmation absolue (« tous », « aucun », « jamais », « toujours ») est fausse dès
  qu'un contre-exemple sourcé existe.
- Ne conclus « faux », « trompeur » ou « imprecis » que si une source citée le montre.
- Si la réponse dépend d'une définition (chômage BIT vs inscrits France Travail, dette brute vs nette, euros
  courants vs constants), dis laquelle tu utilises et juge selon l'interprétation la plus raisonnable.
- Le titre (headline) est factuel et donne le chiffre ou le fait clé, ≤ 90 caractères, sans le verdict
  (ex. « 7,4 % fin 2021, plus bas niveau depuis 2008 »).
- L'explication fait 1 à 3 phrases, en français simple, avec les chiffres et leur source."""

SYSTEM_AGENT = """Tu es vérificateur pour Sic, un service de fact-checking de débats politiques en direct.
On te donne une affirmation prononcée pendant un débat. Vérifie-la avec les outils, puis donne un verdict.
{as_of}
Méthode :
1. Identifie précisément ce qu'il faut vérifier (chiffre, période, périmètre, personne).
2. Utilise d'abord les sources structurées : INSEE (insee_*), Eurostat (eurostat_get), votes de l'Assemblée
   (an_*). Pour les faits historiques, dates, institutions, histoire des lois : Wikipédia (wikipedia_*, version
   datée). Puis web_search / web_read pour le reste (rapports, déclarations, fact-checks déjà publiés).
3. Sois efficace : {max_steps} tours d'appels d'outils au maximum (plusieurs appels en parallèle par tour).
{scale}
{rules}
Quand tu as fini, réponds UNIQUEMENT avec ce JSON :
{{"label": "...", "headline": "...", "explanation": "...", "sources": [numéros des sources [S#] utilisées], "confidence": 0.0-1.0}}"""

SYSTEM_LIBRARY = """Tu es vérificateur pour Sic. Une affirmation vient d'être prononcée dans un débat.
Voici des vérifications déjà publiées avant le débat (bibliothèque). Détermine si l'une d'elles porte sur
la MÊME affirmation (même fait, même personne, même période ; des chiffres légèrement différents restent la
même affirmation si la vérification permet de trancher).
{scale}
{rules}
Réponds en JSON : {{"match": "<id de l'entrée ou null>", "label": "...", "headline": "...", "explanation": "...",
"confidence": 0.0-1.0}}. Si aucune entrée ne correspond, {{"match": null}}."""


FINAL_NUDGE = (
    "Les recherches sont terminées : tu ne peux plus utiliser d'outils. Donne MAINTENANT ton verdict au format JSON "
    "demandé, avec les sources [S#] déjà obtenues. Si elles ne suffisent pas pour trancher, réponds \"inverifiable\" "
    "et explique en une phrase ce qui manque."
)


def _is_verdict_json(content: str) -> bool:
    try:
        data = parse_json(content)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("label") in LABELS


class Verifier:
    def __init__(
        self,
        connectors: list[Connector],
        llm: LLM,
        library: Library | None = None,
        model: str | None = None,
        library_model: str | None = None,
        max_steps: int = 4,
        tool_timeout: float = 35.0,
        thinking: str | None = None,
    ) -> None:
        self.connectors = connectors
        self.llm = llm
        self.library = library
        self.model = model or config.LLM_SMART
        self.library_model = library_model or config.LLM_FAST
        self.max_steps = max_steps
        self.tool_timeout = tool_timeout
        self.thinking = thinking if thinking is not None else config.VERIFY_THINKING
        self._tools: dict[str, Connector] = {}
        self._specs: list[dict[str, Any]] = []
        for c in connectors:
            for spec in c.tools():
                self._tools[spec.name] = c
                self._specs.append(spec.openai())

    async def verify(self, claim: Claim, ctx: VerifyContext) -> Verdict:
        t0 = time.monotonic()
        verdict = None
        if self.library is not None:
            try:
                verdict = await self._from_library(claim, ctx)
            except Exception:
                verdict = None
        if verdict is None:
            try:
                verdict = await self._agent(claim, ctx)
            except Exception as e:  # never break the live feed on one claim
                verdict = Verdict(
                    claim_id=claim.id, label="inverifiable", headline="Vérification impossible pour le moment",
                    explanation=f"Erreur technique pendant la vérification ({type(e).__name__}).", method="none", confidence=0.0,
                )
        verdict.latency_s = round(time.monotonic() - t0, 2)
        return guard(verdict)

    # -- library -------------------------------------------------------------------------------

    async def _from_library(self, claim: Claim, ctx: VerifyContext) -> Verdict | None:
        assert self.library is not None
        hits = self.library.search(f"{claim.claim} {claim.quote}", claim.speaker, ctx.as_of, k=5, min_coverage=0.3)
        if not hits:
            return None
        listing = "\n\n".join(entry_text(e) for e, _ in hits)
        user = f"Date du débat : {ctx.as_of or 'aujourd’hui'}\nAffirmation : « {claim.quote} »\nReformulée : {claim.claim}\n\nBibliothèque :\n{listing}"
        data = await self.llm.json(
            [{"role": "system", "content": SYSTEM_LIBRARY.format(scale=SCALE, rules=RULES)}, {"role": "user", "content": user}],
            self.library_model,
            max_tokens=1200,
        )
        mid = data.get("match") if isinstance(data, dict) else None
        entry: LibraryEntry | None = next((e for e, _ in hits if e.id == mid), None)
        if entry is None or data.get("label") not in LABELS:
            return None
        sources = [
            Source(kind="factcheck", title=f"{r.outlet} — vérification" + (f" ({r.label})" if r.label else ""), url=r.url,
                   publisher=r.outlet, date=r.published)
            for r in entry.refs
        ]
        sources += [Source(kind="official_data", title=u, url=u, publisher=_host(u)) for u in entry.primary_sources[:2]]
        return Verdict(
            claim_id=claim.id, label=data["label"], headline=str(data.get("headline") or "")[:120],
            explanation=str(data.get("explanation") or entry.explanation), sources=sources, method="library",
            # "Relu" only holds if we kept the human-reviewed label, not a model rewrite of it.
            reviewed=entry.reviewed and data["label"] == entry.verdict,
            confidence=_conf(data.get("confidence"), 0.7), library_id=entry.id,
        )

    # -- agent ---------------------------------------------------------------------------------

    async def _agent(self, claim: Claim, ctx: VerifyContext) -> Verdict:
        speaker = ctx.speaker.name if ctx.speaker else claim.speaker
        as_of = (
            f"Mode direct : l'affirmation a été prononcée le {ctx.as_of.isoformat()}. Juge avec les informations disponibles "
            f"à cette date ; ignore tout ce qui est postérieur."
            if ctx.as_of
            else ""
        )
        system = SYSTEM_AGENT.format(as_of=as_of, max_steps=self.max_steps, scale=SCALE, rules=RULES)
        debate = ctx.debate.title if ctx.debate else ""
        user = (
            f"Débat : {debate}\nOrateur : {speaker}\nThème : {claim.topic}\n"
            f"Citation : « {claim.quote} »\nAffirmation à vérifier : {claim.claim}"
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        sources: list[Source] = []
        used_tools: list[str] = []

        for step in range(self.max_steps + 1):
            last = step == self.max_steps
            if last:
                messages.append({"role": "user", "content": FINAL_NUDGE})
            msg = await self.llm.chat(
                messages, self.model, tools=None if last else self._specs, json_mode=last, thinking=self.thinking, max_tokens=3000
            )
            calls = msg.get("tool_calls") or []
            if not calls or last:
                content = msg.get("content") or ""
                if not _is_verdict_json(content):
                    # The model answered in prose (or wants more searches): ask once more for the JSON verdict.
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": FINAL_NUDGE})
                    msg = await self.llm.chat(messages, self.model, json_mode=True, max_tokens=1500)
                    content = msg.get("content") or ""
                return self._final(claim, content, sources, used_tools)
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            results = await asyncio.gather(*(self._run_tool(c, ctx) for c in calls))
            for call, res in zip(calls, results, strict=True):
                used_tools.append(call["function"]["name"])
                numbered = []
                for s in res.sources:
                    sources.append(s)
                    numbered.append(f"[S{len(sources)}] {s.publisher or ''} — {s.title} {s.url or ''}")
                content = res.content[:4500]
                if numbered:
                    content += "\n\nSources citables :\n" + "\n".join(numbered)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})
        raise RuntimeError("unreachable")

    async def _run_tool(self, call: dict[str, Any], ctx: VerifyContext) -> ToolResult:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return ToolResult(content=f"Arguments JSON invalides pour {name}")
        conn = self._tools.get(name)
        if conn is None:
            return ToolResult(content=f"Outil inconnu : {name}")
        try:
            return await asyncio.wait_for(conn.call(name, args, ctx), timeout=self.tool_timeout)
        except TimeoutError:
            return ToolResult(content=f"{name} : délai dépassé")
        except Exception as e:
            return ToolResult(content=f"{name} : erreur {type(e).__name__}: {e}")

    def _final(self, claim: Claim, content: str, sources: list[Source], used_tools: list[str]) -> Verdict:
        try:
            data = parse_json(content)
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        label = data.get("label")
        if label not in LABELS:
            label = "inverifiable"
        cited: list[Source] = []
        for ref in data.get("sources") or []:
            try:
                n = int(str(ref).strip("[]S s"))
            except ValueError:
                continue
            if 1 <= n <= len(sources) and sources[n - 1] not in cited:
                cited.append(sources[n - 1])
        structured = {"insee", "eurostat", "assemblee"}
        conn_names = {getattr(self._tools.get(t), "name", "") for t in used_tools}
        method = "data" if any(s.kind in ("official_data", "parliament") for s in cited) and conn_names & structured else "web"
        if not cited:
            method = "none"
        return Verdict(
            claim_id=claim.id, label=label, headline=str(data.get("headline") or "")[:120],
            explanation=str(data.get("explanation") or ""), sources=cited[:5],
            method=method, confidence=_conf(data.get("confidence"), 0.5),
        )


def dedupe_sources(v: Verdict) -> Verdict:
    seen: set[str] = set()
    out = []
    for s in v.sources:
        key = s.url or s.title
        if key not in seen:
            seen.add(key)
            out.append(s)
    return v if len(out) == len(v.sources) else v.model_copy(update={"sources": out})


STRONG_KINDS = ("official_data", "parliament", "law", "factcheck")
# Reference outlets whose factual reporting can back a negative verdict (with a precise article link).
REPUTABLE_HOSTS = (
    "wikipedia.org", "reuters.com", "afp.com", "apnews.com", "lemonde.fr", "lefigaro.fr", "liberation.fr",
    "francetvinfo.fr", "franceinfo.fr", "lesechos.fr", "la-croix.com", "ouest-france.fr", "publicsenat.fr", "lcp.fr",
    "bfmtv.com", "tf1info.fr", "20minutes.fr", "france24.com", "rfi.fr", "bbc.com", "nytimes.com", "ft.com",
    "lexpress.fr", "lepoint.fr", "nouvelobs.com", "mediapart.fr", "challenges.fr", "latribune.fr", "europe1.fr",
)
_BANNED = [
    (re.compile(r"\bmensonges\b", re.I), "affirmations inexactes"),
    (re.compile(r"\bmensonge\b", re.I), "affirmation inexacte"),
    (re.compile(r"\bmenteu(?:r|se|rs|ses)\b", re.I), "inexact"),
    (re.compile(r"\bmentir\b", re.I), "se tromper"),
]


def _is_strong(s: Source) -> bool:
    """A source that can back a negative verdict: official/parliament/law/fact-check with a precise URL."""
    if s.kind == "library":
        return True
    if not s.url:
        return False
    u = urlparse(s.url)
    precise = bool(u.path.strip("/")) or "oldid=" in (u.query or "")
    if not precise:
        return False
    host = (u.hostname or "").lower()
    return s.kind in STRONG_KINDS or any(host == h or host.endswith("." + h) for h in REPUTABLE_HOSTS)


def _clean_words(text: str) -> str:
    for rx, repl in _BANNED:
        text = rx.sub(repl, text)
    return text


def guard(v: Verdict) -> Verdict:
    """Product rules applied after the model (never trust the prompt alone):
    - no verdict without a cited source; negative verdicts need a strong, precise source;
    - low confidence -> invérifiable; never accuse of lying."""
    v = dedupe_sources(v)
    v = v.model_copy(update={"headline": _clean_words(v.headline), "explanation": _clean_words(v.explanation)})
    if v.label in NEGATIVE and v.sources and not any(_is_strong(s) for s in v.sources):
        return v.model_copy(update={
            "label": "inverifiable",
            "explanation": v.explanation + " (Sources insuffisantes pour un verdict négatif : seules des sources officielles,"
            " parlementaires ou des fact-checks publiés peuvent l'établir.)",
            "confidence": min(v.confidence, 0.4),
        })
    if v.label in NEGATIVE + ("exact", "plutot_exact") and not v.sources:
        return v.model_copy(update={
            "label": "inverifiable",
            "headline": v.headline or "Aucune source fiable trouvée à temps",
            "explanation": (v.explanation + " " if v.explanation else "") + "(Aucune source citable : verdict suspendu.)",
            "confidence": min(v.confidence, 0.3),
        })
    if v.label != "inverifiable" and v.confidence < 0.35:
        return v.model_copy(update={"label": "inverifiable", "explanation": v.explanation + " (Confiance insuffisante.)"})
    if not v.headline:
        v = v.model_copy(update={"headline": v.explanation[:90] or "—"})
    return v


def _conf(x: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return default


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")
