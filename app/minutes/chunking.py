"""Split the refined transcript into target windows with read-only context on both sides."""
from dataclasses import dataclass

from app.schemas.transcript import Transcript, TranscriptSegment


def who(seg: TranscriptSegment) -> str:
    if seg.speaker_known:
        return f"{seg.speaker_name} / {seg.speaker}" if seg.speaker_name else seg.speaker
    return "unknown speaker"


def display(seg: TranscriptSegment) -> str:
    return (seg.speaker_name or seg.speaker) if seg.speaker_known else "unknown speaker"


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def line(seg: TranscriptSegment) -> str:
    return f"[{seg.segment_id}] {mmss(seg.start)} {who(seg)}: {seg.text}"


@dataclass
class Chunk:
    chunk_id: str
    target: list[TranscriptSegment]
    before: list[TranscriptSegment]
    after: list[TranscriptSegment]

    @property
    def all(self) -> list[TranscriptSegment]:
        return self.before + self.target + self.after

    def render(self) -> str:
        parts = []
        if self.before:
            parts.append("CONTEXT BEFORE (read only; do not extract from it):\n" + "\n".join(line(s) for s in self.before))
        parts.append(f"TARGET SEGMENTS ({self.target[0].segment_id} to {self.target[-1].segment_id}); extract from these:\n<transcript>\n"
                     + "\n".join(line(s) for s in self.target) + "\n</transcript>")
        if self.after:
            parts.append("CONTEXT AFTER (read only; do not extract from it):\n" + "\n".join(line(s) for s in self.after))
        return "\n\n".join(parts)


def make_chunks(t: Transcript, size: int, context: int) -> list[Chunk]:
    segs = t.segments
    out = []
    for i in range(0, len(segs), size):
        out.append(Chunk(chunk_id=f"c{len(out) + 1:02d}", target=segs[i : i + size],
                         before=segs[max(0, i - context) : i], after=segs[i + size : i + size + context]))
    return out
