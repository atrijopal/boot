"""Lossless source spans and duration allocation, independent of topic boundaries."""
import hashlib
import json
import math
import re

from app.schemas.phase3 import Unit
from app.schemas.refinement import RefinementResult
from app.utils.errors import PipelineError


def source_revision(result: RefinementResult) -> str:
    payload = {
        "transcript": result.refined_transcript.model_dump(mode="json"),
        "speaker_segments": {k: v.model_dump() for k, v in result.segment_speakers.items()},
        "manual_names": result.manual_names,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _spans(text: str, limit: int):
    # Sentence boundaries first; oversized sentences split at whitespace only.
    starts = [0] + [m.end() for m in re.finditer(r"(?<=[.!?])\s+(?=\S)", text)] + [len(text)]
    for a, b in zip(starts, starts[1:]):
        while a < b:
            end = min(b, a + limit)
            if end < b:
                space = text.rfind(" ", a, end)
                if space > a:
                    end = space + 1
            left = a + len(text[a:end]) - len(text[a:end].lstrip())
            right = end - len(text[a:end]) + len(text[a:end].rstrip())
            if left < right:
                yield left, right
            a = end


def _aligned_words(seg):
    """Only exact token alignment, with bounded monotonic timestamps, is trusted."""
    tokens = list(re.finditer(r"\S+", seg.text))
    if not seg.words or len(tokens) != len(seg.words):
        return None
    clean = lambda s: re.sub(r"[^\w]", "", s).casefold()
    previous = seg.start
    for token, word in zip(tokens, seg.words):
        if (not clean(token.group()) or clean(token.group()) != clean(word.text)
                or not math.isfinite(word.start) or not math.isfinite(word.end)
                or word.start < previous or word.end < word.start or word.end > seg.end):
            return None
        previous = word.end
    return list(zip(tokens, seg.words))


def build_units(result: RefinementResult, max_chars: int = 1200) -> list[Unit]:
    if not result.invariants.all_ok:
        raise PipelineError("PHASE3_INPUT_INVALID", "Phase 2 invariant checks did not pass.")
    revision = source_revision(result)
    ids = set()
    units = []
    for seg in result.refined_transcript.segments:
        if seg.segment_id in ids or not all(map(math.isfinite, [seg.start, seg.end])) or seg.end < seg.start:
            raise PipelineError("PHASE3_INPUT_INVALID", "Duplicate segment IDs or invalid timestamps.")
        ids.add(seg.segment_id)
        spans = list(_spans(seg.text, max_chars))
        lengths = sum(b - a for a, b in spans)
        words = _aligned_words(seg)
        if words and any(t.start() < boundary < t.end() for t, _ in words for span in spans for boundary in span):
            words = None  # an oversized token was split: estimate the whole segment once
        offset = 0.0
        mixed = bool(result.segment_speakers.get(seg.segment_id) and result.segment_speakers[seg.segment_id].mixed)
        for a, b in spans:
            selected = [(t, w) for t, w in words or [] if a <= t.start() < b]
            if selected:
                start, end = selected[0][1].start, selected[-1][1].end
                duration = sum(w.end - w.start for _, w in selected)
                precision = "word"
            else:
                duration = (seg.end - seg.start) * (b - a) / lengths
                start, end = seg.start + offset, seg.start + offset + duration
                precision = "segment_estimate" if duration > 0 else "unavailable"
                offset += duration
            units.append(Unit(
                unit_id=f"u{len(units) + 1:06d}", segment_id=seg.segment_id,
                text=seg.text[a:b], start_char=a, end_char=b,
                speaker_id=seg.speaker if seg.speaker_known and not mixed else None,
                speaker_name=seg.speaker_name if seg.speaker_known and not mixed else None,
                mixed_speakers=mixed, start_seconds=start, end_seconds=end,
                duration_seconds=duration if duration > 0 else None,
                timing_precision=precision, source_revision=revision,
            ))
    if not units:
        raise PipelineError("NO_SPEECH", "There is no accepted transcript to summarize.")
    return units

