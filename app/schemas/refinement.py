from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.transcript import Transcript

Status = Literal["applied", "suggested", "rejected"]


class Evidence(BaseModel):
    kind: str  # elsewhere | second_asr | phase1_term | similarity
    detail: str


class Verdict(BaseModel):
    same_meaning: bool | None = None
    new_information: bool | None = None
    note: str = ""


class Edit(BaseModel):
    edit_id: str
    segment_id: str
    start: float
    end: float
    kind: str  # term | acronym | name | spelling | format
    find: str
    replace: str
    char_start: int | None = None  # offsets of `find` in the NORMALIZED segment text
    char_end: int | None = None
    reason: str = ""  # the LLM's stated reason (untrusted explanation, not evidence)
    status: Status  # decided by the pipeline
    reject_reason: str | None = None
    support: list[Evidence] = Field(default_factory=list)
    similarity: float | None = None
    verdict: Verdict | None = None
    unverified: bool = False  # applied without the verifier (verifier off/unavailable)
    user_decision: Literal["accept", "reject"] | None = None


class SegmentSpeaker(BaseModel):
    speaker: str
    speaker_known: bool
    share: float = 1.0  # fraction of the segment's voiced time attributed to `speaker`
    mixed: bool = False  # another speaker also talks in this segment
    runner_up: str | None = None
    runner_up_share: float = 0.0


class SpeakerName(BaseModel):
    name_id: str
    speaker: str
    name: str
    basis: str  # self_introduction | addressed_by_others | introduced_by_others
    segment_id: str
    start: float
    quote: str  # verbatim evidence from the transcript
    status: Status
    reject_reason: str | None = None
    note: str = ""  # e.g. result of the reply check, or a conflict
    user_decision: Literal["accept", "reject"] | None = None


class InvariantReport(BaseModel):
    segment_count_unchanged: bool
    ids_times_speakers_unchanged: bool
    numbers_unchanged: bool
    dates_times_unchanged: bool
    negations_unchanged: bool
    details: list[str] = Field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all([self.segment_count_unchanged, self.ids_times_speakers_unchanged, self.numbers_unchanged,
                    self.dates_times_unchanged, self.negations_unchanged])


class RefinementResult(BaseModel):
    job_id: str
    refine_model: str
    verify_model: str | None
    second_asr: str | None = None
    edits: list[Edit] = Field(default_factory=list)
    speaker_source: str = "provider"  # provider | transplanted:<name> | none
    segment_speakers: dict[str, SegmentSpeaker] = Field(default_factory=dict)
    speaker_names: list[SpeakerName] = Field(default_factory=list)
    manual_names: dict[str, str] = Field(default_factory=dict)  # speaker label -> name typed by the user
    refined_transcript: Transcript
    invariants: InvariantReport
    stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)
