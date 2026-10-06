from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    segment_id: str
    quote: str  # verbatim from the refined transcript
    start: float
    end: float
    speaker: str
    speaker_name: str | None = None
    char_start: int
    char_end: int


class Verification(BaseModel):
    status: Literal["verified", "needs_review"] = "needs_review"
    notes: list[str] = Field(default_factory=list)
    model: str | None = None


class Item(BaseModel):
    item_id: str = ""
    lens: str
    text: str
    status: str
    attrs: dict[str, Any] = Field(default_factory=dict)  # lens-specific: action owner/deadline, fact label/value/flags
    evidence: list[Evidence]
    start: float = 0.0
    speakers: list[str] = Field(default_factory=list)  # display names (or labels) of whoever is quoted
    chunk_ids: list[str] = Field(default_factory=list)
    verification: Verification = Field(default_factory=Verification)
    superseded_by: str | None = None
    resolved_by: str | None = None
    related: list[str] = Field(default_factory=list)


class Relation(BaseModel):
    source: str
    target: str
    kind: str  # supersedes | contradicts | adopts | rejects | resolves
    segment_id: str
    quote: str


class Claim(BaseModel):
    text: str
    item_ids: list[str] = Field(default_factory=list)
    segment_ids: list[str] = Field(default_factory=list)
    verified: bool = False


class Topic(BaseModel):
    topic_id: str
    title: str
    start_segment: str
    end_segment: str
    start: float
    end: float
    claims: list[Claim] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class Participant(BaseModel):
    speaker: str
    name: str | None = None
    talk_seconds: float
    segments: int


class RunMeta(BaseModel):
    job_id: str
    run_id: str
    source_revision: str
    created_at: str
    duration_seconds: float
    segments: int
    chunks: int
    models: dict[str, str]


class Minutes(BaseModel):
    meta: RunMeta
    status: Literal["complete", "partial"]
    participants: list[Participant] = Field(default_factory=list)
    executive_summary: list[Claim] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)  # verified, merged, final ids
    needs_review: list[Item] = Field(default_factory=list)  # extracted + quote-checked but not confirmed by the verifier
    relations: list[Relation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)
    trace_summary: dict[str, Any] = Field(default_factory=dict)
