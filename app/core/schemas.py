from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.audio import AudioMetadata
from app.schemas.facts import ProtectedFacts, SpeakerStat
from app.schemas.refinement import Edit
from app.schemas.transcript import Transcript


class Evidence(BaseModel):
    uid: str  # the prompt line (a merged run of segments) the quote was found in
    quote: str  # verbatim from the transcript
    start: float
    end: float
    speaker: str
    speaker_name: str | None = None
    segment_ids: list[str]
    char_start: int
    char_end: int


class Record(BaseModel):
    rid: str = ""
    kind: str  # decision | proposal | action | question | risk | fact | disagreement
    status: str
    text: str
    attrs: dict[str, Any] = Field(default_factory=dict)  # action: owner/deadline; fact: label/value
    evidence: list[Evidence]
    start: float = 0.0
    speakers: list[str] = Field(default_factory=list)
    check: Literal["code", "code+model", "needs_review"] = "code"  # what vouches for it: our code checks, plus the small model check, or the model doubted it
    notes: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    text: str
    record_ids: list[str] = Field(default_factory=list)


class Topic(BaseModel):
    title: str
    start: float
    end: float
    first_uid: str
    last_uid: str
    bullets: list[Claim] = Field(default_factory=list)  # written by the review step, each tied to verified records
    outcome: Literal["decided", "open", "info"] | None = None  # how the topic ended, from the review step


class SpeakerName(BaseModel):
    speaker: str
    name: str
    uid: str
    quote: str
    basis: Literal["self", "addressed", "edited"] = "self"  # said by the speaker, said to them by someone who then got a reply, or typed by the user
    role: str | None = None  # job title or role, only if the speaker said it


class NameSuggestion(BaseModel):
    """Phase 2: who a speaker label seems to be, with the quote that shows it. Self-introductions are applied; names said TO someone are only suggested."""
    suggestion_id: str
    speaker: str  # "SPEAKER_01"
    name: str | None = None
    role: str | None = None
    basis: Literal["self", "addressed"]
    quote: str
    uid: str
    start: float
    status: Literal["applied", "suggested"]
    user_decision: Literal["accept", "reject"] | None = None


class Call(BaseModel):
    n: int
    stage: str  # refine | extract | review
    model: str
    effort: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    secs: float = 0.0
    cache_hit: bool = False
    fallback_for: str | None = None  # the model this call was meant for, when it was out of quota or down and FALLBACK_MODEL answered instead
    effort_fallback: bool = False  # the first answer was invalid JSON (the model spent its budget thinking), so the call was repeated at LOW effort: expect a thinner answer
    window: str = ""
    input: str = ""
    output: Any = None
    error: str | None = None


class Usage(BaseModel):
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Result(BaseModel):
    job_id: str
    status: Literal["complete", "partial"] = "complete"
    review_ran: bool = True  # False when the final check (review of decisions and tasks, summary) could not be run
    provider: str
    asr_model: str
    audio: AudioMetadata
    transcript: Transcript  # normalized, as heard
    refined: Transcript  # edits applied, speaker names added
    edits: list[Edit] = Field(default_factory=list)
    records: list[Record] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    headline: Claim | None = None  # one sentence: what the meeting was about and its main outcome
    summary: list[Claim] = Field(default_factory=list)
    decision_context: dict[str, Claim] = Field(default_factory=dict)  # decision id -> one sentence of context built from other records
    speaker_summaries: dict[str, list[Claim]] = Field(default_factory=dict)  # speaker label -> what that person said/proposed/asked/committed to
    speaker_names: list[SpeakerName] = Field(default_factory=list)
    extracted_names: list[SpeakerName] | None = None  # names the extraction step found (phase 3), before phase-2 suggestions and typed names; None in results saved before this existed
    speaker_roles: dict[str, str] = Field(default_factory=dict)  # speaker label -> role they stated
    speakers_inferred: bool = False  # labels came from a model reading the text, not from voices
    meeting_date: str | None = None  # YYYY-MM-DD, typed by the user; only used to turn "by Friday" into a date
    warnings: list[str] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)  # model proposals our checks rejected: {window, kind, text, reason}
    usage: Usage = Field(default_factory=Usage)
    calls: list[Call] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _older_results(self):
        """Results saved before `review_ran` existed recorded a failed review only as a warning."""
        if self.review_ran and any("review step did not run" in w for w in self.warnings):
            self.review_ran = False
        return self


class RareWord(BaseModel):
    word: str
    count: int
    first_start: float


class Phase1(BaseModel):
    """Transcript and the free (no model) extractions."""
    job_id: str
    provider: str
    asr_model: str
    audio: AudioMetadata
    transcript: Transcript  # normalized, as heard
    facts: ProtectedFacts
    rare_words: list[RareWord] = Field(default_factory=list)
    speakers: list[SpeakerStat] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)


class Phase2(BaseModel):
    """Refinement: fixes proposed by the model and gated by code."""
    job_id: str
    status: Literal["complete", "partial", "skipped"] = "complete"
    refined: Transcript
    edits: list[Edit] = Field(default_factory=list)
    name_suggestions: list[NameSuggestion] = Field(default_factory=list)
    speaker_labels: dict[str, str] = Field(default_factory=dict)  # segment_id -> "SPEAKER_A", inferred from the text by a model
    speakers_inferred: bool = False
    calls: list[Call] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)
