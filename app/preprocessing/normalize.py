"""Step 7: conservative normalization. NOT refinement: wording, fillers, numbers and negations are untouched."""
import re

from app.config.settings import settings
from app.schemas.transcript import Transcript, TranscriptSegment

_WS = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%])")
_KEY = re.compile(r"[^a-z0-9]+")


def _clean(text: str) -> str:
    text = _WS.sub(" ", text).strip()
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text)


def _dup_key(text: str) -> str:
    return _KEY.sub(" ", text.lower()).strip()


def normalize_transcript(raw: Transcript) -> Transcript:
    """Return a new Transcript (raw is not modified). Segment ids are preserved for provenance."""
    notes = list(raw.notes)
    kept: list[TranscriptSegment] = []
    for seg in raw.segments:
        text = _clean(seg.text)
        if not text:
            notes.append(f"dropped {seg.segment_id}: empty after whitespace cleanup")
            continue
        # Whisper's own hallucination signature: confident "no speech" AND very low likelihood.
        if (
            seg.no_speech_prob is not None
            and seg.avg_logprob is not None
            and seg.no_speech_prob > settings.no_speech_drop_prob
            and seg.avg_logprob < settings.low_logprob
        ):
            notes.append(f"dropped {seg.segment_id}: likely non-speech (no_speech_prob={seg.no_speech_prob:.2f})")
            continue
        if kept and _dup_key(text) == _dup_key(kept[-1].text) and seg.start < kept[-1].end + 0.5 and seg.speaker == kept[-1].speaker:
            notes.append(f"dropped {seg.segment_id}: duplicate of {kept[-1].segment_id}")
            continue
        kept.append(seg.model_copy(update={"text": text}))
    kept.sort(key=lambda s: (s.start, s.segment_id))
    return raw.model_copy(update={"kind": "normalized", "segments": kept, "notes": notes})
