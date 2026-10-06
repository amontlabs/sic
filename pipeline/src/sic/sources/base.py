"""Connector interface: every data source exposes LLM tools.

The verifier gives the LLM the tool specs of all connectors, the LLM calls them,
and each call returns compact text for the model plus structured `Source`s that
end up cited on the verdict card.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Protocol

from pydantic import BaseModel, Field

from sic.models import Debate, Person, Source


class ToolSpec(BaseModel):
    name: str  # snake_case, unique across connectors
    description: str  # French or English, explain when to use it
    parameters: dict[str, Any]  # JSON schema (type=object)

    def openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class ToolResult(BaseModel):
    content: str  # compact text for the LLM (keep < ~4000 chars)
    sources: list[Source] = Field(default_factory=list)


class VerifyContext(BaseModel):
    as_of: Date | None = None  # judge with data available at this date (strict live simulation)
    debate: Debate | None = None
    speaker: Person | None = None


class Connector(Protocol):
    name: str

    def tools(self) -> list[ToolSpec]: ...

    async def call(self, tool: str, args: dict[str, Any], ctx: VerifyContext) -> ToolResult: ...
