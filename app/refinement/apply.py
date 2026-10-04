"""Apply edits to produce the refined transcript, honouring user decisions, and report invariants."""
from app.refinement.validate import fingerprint
from app.schemas.refinement import Edit, InvariantReport
from app.schemas.transcript import Transcript


def is_applied(e: Edit) -> bool:
    """Effective state: a user decision overrides the pipeline, except that rule-rejected edits can never be applied."""
    if e.status == "rejected":
        return False
    if e.user_decision == "accept":
        return True
    if e.user_decision == "reject":
        return False
    return e.status == "applied"


def render_refined(normalized: Transcript, edits: list[Edit]) -> Transcript:
    """Same segments (ids, times, speakers, words); only `text` changes where edits apply."""
    by_seg: dict[str, list[Edit]] = {}
    for e in edits:
        if is_applied(e) and e.char_start is not None:
            by_seg.setdefault(e.segment_id, []).append(e)
    segs = []
    for s in normalized.segments:
        text = s.text
        last = len(text) + 1
        for e in sorted(by_seg.get(s.segment_id, []), key=lambda x: -x.char_start):
            if e.char_end <= last and text[e.char_start : e.char_end] == e.find:  # skip overlapping / stale edits
                text = text[: e.char_start] + e.replace + text[e.char_end :]
                last = e.char_start
        segs.append(s.model_copy(update={"text": text}))
    return normalized.model_copy(update={"kind": "refined", "segments": segs})


def invariant_report(normalized: Transcript, refined: Transcript) -> InvariantReport:
    details: list[str] = []
    count_ok = len(normalized.segments) == len(refined.segments)
    ids_ok = count_ok and all(
        (a.segment_id, a.start, a.end, a.speaker, a.speaker_known) == (b.segment_id, b.start, b.end, b.speaker, b.speaker_known)
        for a, b in zip(normalized.segments, refined.segments)
    )
    num_ok = dt_ok = neg_ok = True
    if count_ok:
        for a, b in zip(normalized.segments, refined.segments):
            if a.text == b.text:
                continue
            fa, fb = fingerprint(a.text), fingerprint(b.text)
            for key in ("numbers", "dates_times", "negations"):
                if fa[key] != fb[key]:
                    details.append(f"{a.segment_id}: {key} changed")
                    if key == "numbers":
                        num_ok = False
                    elif key == "dates_times":
                        dt_ok = False
                    else:
                        neg_ok = False
    return InvariantReport(
        segment_count_unchanged=count_ok, ids_times_speakers_unchanged=ids_ok,
        numbers_unchanged=num_ok, dates_times_unchanged=dt_ok, negations_unchanged=neg_ok, details=details,
    )
