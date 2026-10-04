from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low", "unknown"]


class FactItem(BaseModel):
    """One extracted mention with provenance back to its transcript segment.

    char_start/char_end index into the *normalized* segment text with the same segment_id.
    """

    type: str
    value: str
    surface: str
    segment_id: str
    start: float
    end: float
    char_start: int | None = None
    char_end: int | None = None
    confidence: Confidence = "unknown"
    source: str
    extra: dict = Field(default_factory=dict)


class Mention(BaseModel):
    segment_id: str
    start: float
    end: float
    char_start: int | None = None
    char_end: int | None = None


class TermFact(BaseModel):
    """A technical term / acronym discovered in this meeting (aggregated over mentions)."""

    term: str
    kind: str  # acronym | spelled_acronym | rare_word | keyphrase
    sources: list[str]
    count: int
    confidence: Confidence = "unknown"
    asr_suspect: bool = False
    asr_suspect_reason: str | None = None
    zipf: float | None = None
    mentions: list[Mention] = Field(default_factory=list)


class ProtectedFacts(BaseModel):
    numbers: list[FactItem] = Field(default_factory=list)
    dates: list[FactItem] = Field(default_factory=list)
    times: list[FactItem] = Field(default_factory=list)
    durations: list[FactItem] = Field(default_factory=list)
    people: list[FactItem] = Field(default_factory=list)
    organizations: list[FactItem] = Field(default_factory=list)
    locations: list[FactItem] = Field(default_factory=list)
    other_entities: list[FactItem] = Field(default_factory=list)
    proper_noun_candidates: list[FactItem] = Field(default_factory=list)
    technical_terms: list[TermFact] = Field(default_factory=list)
    acronyms: list[TermFact] = Field(default_factory=list)
    negations: list[FactItem] = Field(default_factory=list)
    # Semantic *candidates*: heuristic cues, never confirmed decisions/tasks.
    candidate_questions: list[FactItem] = Field(default_factory=list)
    candidate_commitments: list[FactItem] = Field(default_factory=list)
    candidate_proposals: list[FactItem] = Field(default_factory=list)
    candidate_decisions: list[FactItem] = Field(default_factory=list)
    candidate_tasks: list[FactItem] = Field(default_factory=list)
    candidate_rejections: list[FactItem] = Field(default_factory=list)
    uncertainty: list[FactItem] = Field(default_factory=list)


class SpeakerStat(BaseModel):
    speaker: str
    speaker_known: bool
    talk_seconds: float
    segments: int
    words: int


class EntitySummary(BaseModel):
    text: str
    type: str
    count: int
    first_start: float


class TopicHint(BaseModel):
    start: float
    end: float
    terms: list[str]


class MeetingContext(BaseModel):
    duration_seconds: float
    segment_count: int
    word_count: int
    speakers: list[SpeakerStat]
    entities: list[EntitySummary]
    terms: list[str]
    topic_hints: list[TopicHint]
    notes: list[str] = Field(default_factory=list)
