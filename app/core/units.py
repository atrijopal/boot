"""Compact transcript for the model: consecutive short lines by the same speaker are merged into one 'unit' with one id.

Fewer ids and speaker tags means fewer tokens, and every quote is still checked against the real transcript text:
a unit's text is exactly the original segments joined by a single space, with offsets back to each segment.
"""
from dataclasses import dataclass, field

from app.schemas.transcript import Transcript

MAX_UNIT_CHARS = 360
MAX_GAP_SECONDS = 2.5


@dataclass
class Unit:
    uid: str
    speaker: str  # "SPEAKER_01" or "SPEAKER_UNKNOWN"
    tag: str  # short label used in the prompt: A, B, C... or ?
    start: float
    end: float
    seg_idx: list[int]
    text: str
    offsets: list[tuple[int, int]] = field(default_factory=list)  # (start, end) of each segment inside `text`


def build_units(t: Transcript, max_chars: int = MAX_UNIT_CHARS, max_gap: float = MAX_GAP_SECONDS) -> tuple[list[Unit], dict[str, str]]:
    """Returns (units, legend {tag: speaker label}). Speakers are tagged A, B, C... in order of first appearance."""
    tags: dict[str, str] = {}

    def tag_of(seg) -> str:
        if not seg.speaker_known:
            return "?"
        return tags.setdefault(seg.speaker, chr(ord("A") + len(tags)) if len(tags) < 26 else f"S{len(tags)}")

    units: list[Unit] = []
    for i, seg in enumerate(t.segments):
        tag = tag_of(seg)
        last = units[-1] if units else None
        if last and last.tag == tag and seg.start - last.end <= max_gap and len(last.text) + 1 + len(seg.text) <= max_chars:
            start = len(last.text) + 1
            last.text += " " + seg.text
            last.offsets.append((start, start + len(seg.text)))
            last.seg_idx.append(i)
            last.end = seg.end
        else:
            units.append(Unit(uid=f"u{len(units) + 1:03d}", speaker=seg.speaker if seg.speaker_known else "SPEAKER_UNKNOWN", tag=tag,
                              start=seg.start, end=seg.end, seg_idx=[i], text=seg.text, offsets=[(0, len(seg.text))]))
    return units, {v: k for k, v in tags.items()}


def line(u: Unit) -> str:
    return f"{u.uid} {u.tag}: {u.text}"


@dataclass
class Window:
    wid: str
    target: list[Unit]
    context: list[Unit]  # read-only lines before the window

    def render(self) -> str:
        parts = []
        if self.context:
            parts.append("CONTEXT (do not extract from it):\n" + "\n".join(line(u) for u in self.context))
        parts.append("TRANSCRIPT:\n" + "\n".join(line(u) for u in self.target))
        return "\n\n".join(parts)

    @property
    def chars(self) -> int:
        return sum(len(line(u)) + 1 for u in self.target + self.context)


def make_windows(units: list[Unit], window_chars: int, overlap: int) -> list[Window]:
    out: list[Window] = []
    i = 0
    while i < len(units):
        j, size = i, 0
        while j < len(units) and (j == i or size + len(line(units[j])) + 1 <= window_chars):
            size += len(line(units[j])) + 1
            j += 1
        out.append(Window(wid=f"w{len(out) + 1}", target=units[i:j], context=units[max(0, i - overlap) : i]))
        i = j
    return out


def segment_of(u: Unit, a: int) -> int:
    """Index (into the transcript) of the segment holding character offset `a` of the unit's text."""
    for k, (s, e) in zip(u.seg_idx, u.offsets):
        if s <= a < e + 1:
            return k
    return u.seg_idx[-1]
