from pydantic import BaseModel, Field

from app.schemas.audio import AudioMetadata
from app.schemas.facts import MeetingContext, ProtectedFacts
from app.schemas.transcript import Transcript


class ProcessingResult(BaseModel):
    status: str = "success"
    job_id: str
    provider: str
    model: str
    audio_metadata: AudioMetadata
    raw_transcript: Transcript
    normalized_transcript: Transcript
    meeting_context: MeetingContext
    protected_facts: ProtectedFacts
    timings_seconds: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    cache_hit: bool = False
