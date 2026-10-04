from pydantic import BaseModel, Field


class Word(BaseModel):
    text: str
    start: float
    end: float
    confidence: float | None = None  # only when the ASR provides it
    speaker: str | None = None


class TranscriptSegment(BaseModel):
    segment_id: str
    speaker: str = "SPEAKER_UNKNOWN"
    speaker_known: bool = False
    speaker_name: str | None = None  # Phase 2: only set from evidence in the transcript or by the user
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    confidence: float | None = None
    language: str | None = None


class Transcript(BaseModel):
    provider: str
    model: str
    language: str = "en"
    kind: str = "raw"  # raw | normalized
    duration_seconds: float = 0.0
    speakers_known: bool = False
    segments: list[TranscriptSegment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # normalization log / provider warnings

    def full_text(self) -> str:
        return "\n".join(s.text for s in self.segments)
