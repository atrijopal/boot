"""Helpers shared by extractors: provenance-carrying FactItem construction."""
import re

from app.schemas.facts import Confidence, FactItem
from app.schemas.transcript import TranscriptSegment

_word_pos_cache: dict[tuple, list[tuple[int, int, float, float]]] = {}


def _word_positions(seg: TranscriptSegment) -> list[tuple[int, int, float, float]]:
    """Locate each ASR word inside seg.text (sequential search) -> (char_start, char_end, t0, t1)."""
    key = (seg.segment_id, seg.text, len(seg.words))
    if key in _word_pos_cache:
        return _word_pos_cache[key]
    out, cursor = [], 0
    for w in seg.words:
        tok = w.text.strip()
        if not tok:
            continue
        i = seg.text.find(tok, cursor)
        if i < 0:
            continue
        out.append((i, i + len(tok), w.start, w.end))
        cursor = i + len(tok)
    if len(_word_pos_cache) > 5000:
        _word_pos_cache.clear()
    _word_pos_cache[key] = out
    return out


def span_times(seg: TranscriptSegment, cs: int, ce: int) -> tuple[float, float]:
    """Timestamps for a character span: from word timestamps if aligned, else the segment's."""
    hit = [(t0, t1) for (a, b, t0, t1) in _word_positions(seg) if a < ce and b > cs]
    if hit:
        return min(h[0] for h in hit), max(h[1] for h in hit)
    return seg.start, seg.end


def make_item(
    seg: TranscriptSegment,
    type_: str,
    value: str,
    cs: int,
    ce: int,
    *,
    confidence: Confidence,
    source: str,
    extra: dict | None = None,
    surface: str | None = None,
) -> FactItem:
    t0, t1 = span_times(seg, cs, ce)
    return FactItem(
        type=type_,
        value=value,
        surface=surface if surface is not None else seg.text[cs:ce],
        segment_id=seg.segment_id,
        start=round(t0, 3),
        end=round(t1, 3),
        char_start=cs,
        char_end=ce,
        confidence=confidence,
        source=source,
        extra=extra or {},
    )


def resolve_overlaps(spans: list[tuple[int, int, int, object]]) -> list[tuple[int, int, int, object]]:
    """Keep the longest span among overlapping ones. spans = (start, end, priority, payload)."""
    ordered = sorted(spans, key=lambda s: (-(s[1] - s[0]), s[2], s[0]))
    kept: list[tuple[int, int, int, object]] = []
    for s in ordered:
        if all(s[1] <= k[0] or s[0] >= k[1] for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s[0])


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
