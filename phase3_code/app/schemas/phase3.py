"""Phase 3 contracts. All character ranges are half-open into accepted text."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "phase3-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceRef(StrictModel):
    unit_id: str
    quote: str = Field(min_length=1)
    supports_fields: list[str] = Field(default_factory=list)
    segment_id: str = ""
    start_char: int = 0
    end_char: int = 0
    source_revision: str = ""


class Unit(StrictModel):
    unit_id: str
    segment_id: str
    text: str
    start_char: int
    end_char: int
    speaker_id: str | None
    speaker_name: str | None = None
    mixed_speakers: bool = False
    start_seconds: float
    end_seconds: float
    duration_seconds: float | None
    timing_precision: Literal["word", "segment_estimate", "unavailable"]
    source_revision: str
    memberships: dict[str, float] = Field(default_factory=dict)
    unresolved_weight: float = 1.0
    membership_calibrated: bool = False


class Topic(StrictModel):
    topic_id: str
    title: str
    exemplar_unit_ids: list[str]
    unit_ids: list[str] = Field(default_factory=list)
    alias_topic_ids: list[str] = Field(default_factory=list)
    occurrences: list[list[str]] = Field(default_factory=list)
    duration_seconds: float = 0.0
    duration_share: float | None = None
    priority: float = 0.0
    narrative_words: int = 0


Kind = Literal["claim", "proposal", "decision", "action", "question", "risk"]
STATUSES = {
    "claim": {"stated", "uncertain"},
    "proposal": {"proposed", "adopted", "rejected", "withdrawn", "uncertain"},
    "decision": {"confirmed", "superseded", "revoked", "uncertain"},
    "action": {"requested", "assigned", "committed", "cancelled", "reported_completed", "uncertain"},
    "question": {"open", "resolved", "uncertain"},
    "risk": {"stated", "uncertain"},
}


class Event(StrictModel):
    event_id: str = ""
    anchor_unit_id: str
    kind: Kind
    description: str = Field(min_length=1, max_length=800)
    status: str
    evidence: list[SourceRef] = Field(min_length=1, max_length=12)
    owner_speaker_id: str | None = None
    owner_text: str | None = None  # exact mentioned name; never an invented attendee
    deadline_text: str | None = None  # no inferred calendar date
    topic_ids: list[str] = Field(default_factory=list)
    validation_status: Literal["pending", "supported", "needs_review"] = "pending"
    validation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_status(self):
        if self.status not in STATUSES[self.kind]:
            raise ValueError(f"Invalid status for {self.kind}")
        if self.kind != "action" and any((self.owner_speaker_id, self.owner_text, self.deadline_text)):
            raise ValueError("Only actions have owner and deadline fields")
        return self


class Relation(StrictModel):
    relation_id: str = ""
    source: str
    target: str
    kind: Literal["SUPERSEDES", "CONTRADICTS", "ADOPTS", "REJECTS", "RESOLVES", "DEPENDS_ON"]
    evidence: list[SourceRef] = Field(min_length=1, max_length=12)


class SummaryClaim(StrictModel):
    text: str = Field(min_length=1, max_length=1200)
    event_ids: list[str] = Field(min_length=1)
    evidence: list[SourceRef] = Field(default_factory=list)
    validation_status: Literal["supported"] = "supported"


class TopicSummary(StrictModel):
    topic_id: str
    claims: list[SummaryClaim] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)


class GraphNode(StrictModel):
    node_id: str
    kind: str
    label: str
    data: dict = Field(default_factory=dict)


class GraphEdge(StrictModel):
    edge_id: str
    source: str
    target: str
    kind: str
    evidence: list[SourceRef] = Field(default_factory=list)
    derived: bool = False


class KnowledgeGraph(StrictModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class Manifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str
    run_id: str
    source_revision: str
    configuration: dict
    prompt_hashes: dict[str, str]
    status: Literal["running", "complete", "partial", "stale", "failed"] = "running"
    stage: str = "starting"
    total_units: int = 0
    covered_unit_ids: list[str] = Field(default_factory=list)
    failed_batches: list[str] = Field(default_factory=list)
    started_at: str
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class Phase3Result(StrictModel):
    manifest: Manifest
    units: list[Unit]
    topics: list[Topic]
    events: list[Event]
    relations: list[Relation]
    knowledge_graph: KnowledgeGraph
    topic_summaries: list[TopicSummary]
    executive_summary: list[SummaryClaim]
    duration_accounting: dict
    validation_report: dict
