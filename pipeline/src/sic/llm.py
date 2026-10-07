"""z.ai (GLM) chat client: OpenAI-compatible, async, retries, optional disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
from typing import Any

import httpx

from sic import config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        concurrency: int | None = None,
        cache: bool | None = None,
        timeout: float = 120.0,
        max_attempts: int = 12,
    ) -> None:
        """`max_attempts`/`timeout`: keep them small for live use (a stuck call must not stall the feed)."""
        self.api_key = api_key or config.ZAI_API_KEY
        self.base_url = (base_url or config.ZAI_BASE_URL).rstrip("/")
        self._sem = asyncio.Semaphore(concurrency or int(os.environ.get("SIC_LLM_CONCURRENCY", "6")))
        self._client: httpx.AsyncClient | None = None
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._closed = False
        self.cache = cache if cache is not None else os.environ.get("SIC_LLM_CACHE", "1") == "1"
        self.cache_dir = config.CACHE / "llm"
        self.usage = {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

    def _http(self) -> httpx.AsyncClient:
        if self._closed:
            raise LLMError("LLM client is closed")
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    def _cache_read(self, path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None  # missing or corrupt entry = miss

    def _cache_write(self, path, out: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{random.randrange(1 << 30)}.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False))
        tmp.replace(path)

    def _key(self, body: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        thinking: str | None = None,  # None/"disabled" or "enabled"
        temperature: float = 0.1,
        max_tokens: int = 4096,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Return the assistant message dict ({content, tool_calls?})."""
        if not self.api_key:
            raise LLMError("ZAI_API_KEY is not set (put it in .env at the repo root)")
        body: dict[str, Any] = {
            "model": model or config.LLM_FAST,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if thinking == "enabled" else "disabled"},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        key = self._key(body)
        path = self.cache_dir / key[:2] / f"{key}.json"
        if self.cache and use_cache:
            hit = self._cache_read(path)
            if hit is not None:
                self.usage["cached"] += 1
                return hit

        delay = 1.0
        last_err: Exception | None = None
        for _attempt in range(self.max_attempts):
            async with self._sem:
                t0 = time.monotonic()
                try:
                    r = await self._http().post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    )
                except httpx.HTTPError as e:
                    last_err = e
                    r = None
                self.usage["seconds"] += time.monotonic() - t0
            if r is not None and r.status_code == 200:
                try:
                    data = r.json()
                    choice = data["choices"][0]
                    msg = choice["message"]
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    last_err = LLMError(f"malformed z.ai response: {e}")
                    await asyncio.sleep(delay)
                    continue
                u = data.get("usage") or {}
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                out = {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
                finished = choice.get("finish_reason") in (None, "stop", "tool_calls")
                if not finished:
                    self.usage["truncated"] = self.usage.get("truncated", 0) + 1
                # Only cache complete, non-empty answers: a bad response must not be replayed forever.
                if self.cache and use_cache and finished and (out["content"].strip() or out["tool_calls"]):
                    self._cache_write(path, out)
                return out
            if r is not None and r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                raise LLMError(f"z.ai {r.status_code}: {r.text[:500]}")
            if r is not None:
                last_err = LLMError(f"z.ai {r.status_code}: {r.text[:200]}")
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 30)
        raise LLMError(f"z.ai request failed after retries: {last_err}")

    async def json(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        thinking: str | None = None,
        max_tokens: int = 4096,
    ) -> Any:
        """Chat and parse a JSON answer (one repair retry)."""
        msg = await self.chat(messages, model, json_mode=True, thinking=thinking, max_tokens=max_tokens)
        try:
            return parse_json(msg["content"])
        except ValueError:
            repair = messages + [
                {"role": "assistant", "content": msg["content"]},
                {"role": "user", "content": "Ta réponse n'est pas du JSON valide. Renvoie uniquement le JSON demandé."},
            ]
            msg = await self.chat(repair, model, json_mode=True, thinking=thinking, max_tokens=max_tokens)
            return parse_json(msg["content"])


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> Any:
    t = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Take the outermost object/array.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a : b + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"not JSON: {text[:200]}")


_default: LLM | None = None


def get_llm() -> LLM:
    global _default
    if _default is None:
        _default = LLM()
    return _default
