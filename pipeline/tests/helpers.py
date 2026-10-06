"""Test doubles shared by the core tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from sic.models import Debate, Person, Segment, Word
from sic.sources.base import ToolResult, ToolSpec, VerifyContext


class FakeLLM:
    """Scripted LLM: `responder(messages, kwargs)` returns the assistant message dict."""

    def __init__(self, responder: Callable[[list[dict], dict], dict]) -> None:
        self.responder = responder
        self.calls: list[tuple[list[dict], dict]] = []
        self.usage: dict[str, Any] = {"calls": 0}

    async def chat(self, messages, model=None, **kw) -> dict:
        self.calls.append((messages, kw | {"model": model}))
        self.usage["calls"] += 1
        return self.responder(messages, kw)

    async def json(self, messages, model=None, **kw) -> Any:
        msg = await self.chat(messages, model, json_mode=True, **kw)
        return json.loads(msg["content"])

    async def aclose(self) -> None:
        pass


def debate() -> Debate:
    return Debate(
        id="t",
        title="Débat test",
        date=date(2022, 4, 20),
        roster=[
            Person(id="a", name="Alice Martin", role="candidate", party="P1", aliases=["Madame Martin"]),
            Person(id="b", name="Bruno Petit", role="candidate", party="P2", aliases=["Monsieur Petit"]),
            Person(id="m", name="Mod Erateur", role="moderator"),
        ],
    )


def seg(i: int, speaker: str, text: str, start: float, dur: float = 5.0) -> Segment:
    toks = text.split()
    step = dur / len(toks)
    words = [Word(start=start + k * step, end=start + (k + 1) * step, text=t) for k, t in enumerate(toks)]
    return Segment(id=f"s{i:05d}", start=start, end=start + dur, text=text, speaker=speaker, words=words)


class FakeConnector:
    name = "insee"

    def __init__(self, content: str = "Taux de chômage T4 2021 : 7,4 %", sources=None) -> None:
        from sic.models import Source

        self.content = content
        self.sources = sources if sources is not None else [
            Source(kind="official_data", title="Taux de chômage BIT", url="https://www.insee.fr/fr/statistiques/serie/001688527", publisher="INSEE", date="2021-Q4")
        ]
        self.calls: list[tuple[str, dict, VerifyContext]] = []

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(name="insee_get_series", description="série", parameters={"type": "object", "properties": {"idbank": {"type": "string"}}})]

    async def call(self, tool: str, args: dict, ctx: VerifyContext) -> ToolResult:
        self.calls.append((tool, args, ctx))
        return ToolResult(content=self.content, sources=self.sources)
