"""Shared data contracts. The web app mirrors these in web/src/types.ts."""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, Field

VerdictLabel = Literal["exact", "plutot_exact", "imprecis", "trompeur", "faux", "inverifiable"]
ClaimKind = Literal["chiffre", "vote", "loi", "historique", "comparaison", "citation", "autre"]
SourceKind = Literal["official_data", "parliament", "law", "factcheck", "library", "web"]
Role = Literal["candidate", "moderator", "guest"]

VERDICT_LABELS_FR: dict[str, str] = {
    "exact": "Exact",
    "plutot_exact": "Plutôt exact",
    "imprecis": "Imprécis",
    "trompeur": "Trompeur",
    "faux": "Faux",
    "inverifiable": "Invérifiable",
}


class Person(BaseModel):
    id: str  # stable slug, e.g. "lepen"
    name: str
    role: Role
    party: str | None = None
    aliases: list[str] = Field(default_factory=list)  # how others address them: "Madame Le Pen"
    an_id: str | None = None  # Assemblée nationale acteur uid (e.g. "PA1234") if they were a deputy


class Media(BaseModel):
    youtube_id: str | None = None
    file: str | None = None  # path relative to repo root (dev only, never published)
    duration: float | None = None


class Debate(BaseModel):
    id: str
    title: str
    date: Date
    broadcaster: str | None = None
    roster: list[Person]
    media: Media = Field(default_factory=Media)
    language: str = "fr"


class Word(BaseModel):
    start: float
    end: float
    text: str
    prob: float | None = None


class Segment(BaseModel):
    """One speaker turn fragment (roughly a sentence)."""

    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None  # roster id ("lepen") or anonymous cluster ("spk3")
    words: list[Word] = Field(default_factory=list)


class Source(BaseModel):
    kind: SourceKind
    title: str
    url: str | None = None
    publisher: str | None = None
    date: str | None = None  # ISO date of publication / data period
    excerpt: str | None = None  # short excerpt or the data point used


class Claim(BaseModel):
    id: str
    speaker: str
    start: float
    end: float
    quote: str  # what was said, verbatim span from the transcript
    claim: str  # standalone, decontextualised reformulation (French)
    topic: str
    kind: ClaimKind = "autre"
    checkable: bool = True
    priority: int = 2  # 1 = high (numbers, votes), 3 = low
    segment_ids: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    claim_id: str
    label: VerdictLabel
    headline: str  # <= ~90 chars, shown on the card ("A voté pour, scrutin n° 1234")
    explanation: str  # 1-3 sentences, French
    sources: list[Source] = Field(default_factory=list)
    method: Literal["library", "data", "web", "none"] = "none"
    reviewed: bool = False  # True only when a human (or a human-reviewed library entry) backs it
    confidence: float = 0.5
    library_id: str | None = None
    latency_s: float | None = None  # processing time from end of claim to verdict


EventType = Literal["segment", "claim", "verdict", "speaker", "topic", "status"]


class Event(BaseModel):
    seq: int
    type: EventType
    t: float  # media time the event refers to
    at: float  # media time at which the event became available (live simulation clock)
    data: dict[str, Any]


class Run(BaseModel):
    """Full processed artifact for one debate (what the replay page loads)."""

    debate: Debate
    segments: list[Segment] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
