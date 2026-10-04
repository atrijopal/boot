"""Phase 2 orchestration: Phase-1 output -> proposed edits -> gate -> verify -> refined transcript + change log."""
import csv
import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from app.config.settings import settings
from app.refinement.apply import invariant_report, is_applied, render_refined
from app.refinement.llm_edits import propose_edits
from app.refinement.second_asr import disagreements, heard_by_segment, run_second_asr
from app.refinement.speakers import apply_names, apply_speakers, effective_names, from_provider, gate_names, propose_names, transplant
from app.refinement.validate import Support, build_phase1_terms, check_edit, protected_spans
from app.refinement.verify import verify_edits
from app.schemas.refinement import Edit, RefinementResult, Verdict
from app.schemas.result import ProcessingResult
from app.utils.errors import PipelineError
from app.utils.logging import get_logger, timed

log = get_logger("refine_pipeline")
StatusFn = Callable[[str, str], None]


def job_dir(job_id: str) -> Path:
    return Path(settings.outputs_dir) / job_id


def load_phase1(job_id: str) -> ProcessingResult:
    f = job_dir(job_id) / "phase1_result.json"
    if not f.is_file():
        raise PipelineError("PHASE1_MISSING", "Run Phase 1 on this recording first (no saved result for this job).")
    return ProcessingResult.model_validate_json(f.read_text())


def load_refinement(job_id: str) -> RefinementResult:
    f = job_dir(job_id) / "refinement.json"
    if not f.is_file():
        raise PipelineError("REFINEMENT_MISSING", "This job has not been refined yet.")
    return RefinementResult.model_validate_json(f.read_text())


def run_refinement(job_id: str, status: StatusFn | None = None) -> RefinementResult:
    say = status or (lambda s, m: None)
    timings: dict[str, float] = {}
    warnings: list[str] = []
    p1 = load_phase1(job_id)
    normalized, facts = p1.normalized_transcript, p1.protected_facts
    if not normalized.segments:
        raise PipelineError("NO_SPEECH", "There is no transcript to refine.")

    with timed(timings, "total", log):
        heard: dict[str, str] = {}
        diffs: dict[str, list[dict]] = {}
        second_name = None
        second = None
        if settings.refine_use_second_asr:
            say("second_asr", "Getting a second opinion from the other ASR")
            with timed(timings, "second_asr", log):
                second, second_name, warn = run_second_asr(p1.provider, p1.audio_metadata.source_path, p1.audio_metadata.sha256)
            if warn:
                warnings.append(warn)
            if second:
                heard = heard_by_segment(normalized, second)
                diffs = {s.segment_id: disagreements(s, heard.get(s.segment_id, "")) for s in normalized.segments}

        # who speaks: acoustic labels only (provider's own, or copied from the diarizing ASR by timestamp)
        if normalized.speakers_known:
            assignments, speaker_source = from_provider(normalized), "provider"
        elif second is not None and second.speakers_known:
            assignments, speaker_source = transplant(normalized, second), f"transplanted:{second_name}"
            if not assignments:
                speaker_source = "none"
        else:
            assignments, speaker_source = {}, "none"
        if speaker_source == "none":
            warnings.append("No speaker labels available: use Deepgram (or provide a Deepgram key so it can label Groq's text).")
        base = apply_speakers(normalized, assignments)

        say("proposing", f"Asking {settings.refine_model} for corrections")
        with timed(timings, "propose", log):
            raw_edits, warn = propose_edits(normalized, facts, diffs)
            warnings += warn

        say("validating", "Checking every proposed edit against the protected facts")
        with timed(timings, "validate", log):
            edits = _gate(normalized, facts, heard, raw_edits)

        verify_model = settings.verify_model if settings.verify_enabled else None
        to_verify = [e for e in edits if e.status == "applied"]
        if to_verify and settings.verify_enabled:
            say("verifying", f"Verifying {len(to_verify)} edit(s) with {settings.verify_model}")
            with timed(timings, "verify", log):
                _verify(normalized, edits, to_verify, warnings)
        else:
            for e in to_verify:
                e.unverified = True

        refined = render_refined(base, edits)
        report = invariant_report(base, refined)
        if not report.all_ok:  # should be impossible after the gate; refuse to ship if it happens
            raise PipelineError("INVARIANT_VIOLATION", "Internal check failed: refinement changed protected facts. " + "; ".join(report.details[:3]))

        names = []
        if any(sg.speaker_known for sg in refined.segments):
            say("speakers", "Looking for speaker names stated in the transcript")
            with timed(timings, "speaker_names", log):
                raw_names, warn = propose_names(refined)
                warnings += warn
                names = gate_names(refined, raw_names)
        refined = apply_names(refined, effective_names(names, {}))

    stats = _stats(edits, raw_edits, refined, normalized)
    stats["speakers_named"] = len(effective_names(names, {}))
    result = RefinementResult(
        job_id=job_id, refine_model=settings.refine_model, verify_model=verify_model, second_asr=second_name,
        edits=edits, speaker_source=speaker_source, segment_speakers=assignments, speaker_names=names,
        refined_transcript=refined, invariants=report, stats=stats, warnings=warnings, timings_seconds=timings,
    )
    save_artifacts(job_id, result, normalized)
    log.info("refine %s: %s", job_id, stats)
    return result


def _gate(normalized, facts, heard, raw_edits) -> list[Edit]:
    sup = Support(transcript=normalized, phase1_terms=build_phase1_terms(facts), second_text=heard)
    by_seg = {s.segment_id: s for s in normalized.segments}
    grouped: dict[str, list[dict]] = {}
    seen = set()
    for r in raw_edits:
        key = (r.get("segment_id"), r.get("find"), r.get("replace"))
        if key not in seen:
            seen.add(key)
            grouped.setdefault(r["segment_id"], []).append(r)
    out: list[Edit] = []
    for seg_id in sorted(grouped, key=lambda k: by_seg[k].start if k in by_seg else 0):
        seg = by_seg.get(seg_id)
        if seg is None:
            continue
        prot = protected_spans(facts, seg_id)
        taken: list[tuple[int, int]] = []
        changed = 0
        for r in grouped[seg_id]:
            status, why, support, sim, span = check_edit(seg, r, sup, prot, taken, changed, len(taken))
            if status != "rejected" and span:
                taken.append(span)
                changed += max(len(r["find"]), len(r["replace"]))
            find, replace = str(r.get("find", "")), str(r.get("replace", ""))
            out.append(Edit(
                edit_id="", segment_id=seg_id, start=seg.start, end=seg.end, kind=str(r.get("kind", "")).lower(),
                find=find, replace=replace, char_start=span[0] if span else None, char_end=span[1] if span else None,
                reason=str(r.get("reason", ""))[:300], status=status, reject_reason=why, support=support, similarity=sim,
            ))
    for i, e in enumerate(out, 1):
        e.edit_id = f"edt_{i:03d}"
    return out


def _verify(normalized, edits, to_verify, warnings) -> None:
    by_seg = {s.segment_id: s for s in normalized.segments}
    items = []
    for e in to_verify:
        before = by_seg[e.segment_id].text
        items.append((e, before, before[: e.char_start] + e.replace + before[e.char_end :]))
    verdicts, warn = verify_edits(items)
    warnings += warn
    for e in to_verify:
        v: Verdict | None = verdicts.get(e.edit_id)
        e.verdict = v
        if v is None:
            e.unverified = True
        elif not v.same_meaning or v.new_information:
            e.status = "suggested"  # a checker doubts it: human decides


def _stats(edits, raw_edits, refined, normalized) -> dict[str, int]:
    c = Counter(e.status for e in edits)
    return {
        "proposed": len(raw_edits), "applied": c["applied"], "suggested": c["suggested"], "rejected": c["rejected"],
        "segments_total": len(normalized.segments),
        "segments_changed": sum(1 for a, b in zip(normalized.segments, refined.segments) if a.text != b.text),
    }


# ---------------------------------------------------------------- user decisions + artifacts
def _recompute(res: RefinementResult, p1: ProcessingResult) -> None:
    """Re-render the refined transcript from the current decisions. Raises PipelineError(INVARIANT_VIOLATION) if unsafe."""
    base = apply_speakers(p1.normalized_transcript, res.segment_speakers)
    refined = render_refined(base, res.edits)
    report = invariant_report(base, refined)
    if not report.all_ok:
        raise PipelineError("INVARIANT_VIOLATION", "Accepting this edit would change protected facts: " + "; ".join(report.details[:3]))
    res.refined_transcript = apply_names(refined, effective_names(res.speaker_names, res.manual_names))
    res.invariants = report
    res.stats = _stats_from(res, p1)


def set_decision(job_id: str, edit_id: str, decision: str) -> RefinementResult:
    res = load_refinement(job_id)
    p1 = load_phase1(job_id)
    edit = next((e for e in res.edits if e.edit_id == edit_id), None)
    if edit is None:
        raise PipelineError("EDIT_NOT_FOUND", f"No edit '{edit_id}' in this job.")
    if decision not in ("accept", "reject", "reset"):
        raise PipelineError("BAD_DECISION", "Decision must be accept, reject or reset.")
    if decision == "accept" and edit.status == "rejected":
        raise PipelineError("EDIT_REJECTED", f"This edit is blocked and cannot be accepted: {edit.reject_reason}")
    previous = edit.user_decision
    edit.user_decision = None if decision == "reset" else decision  # type: ignore[assignment]
    try:
        _recompute(res, p1)
    except PipelineError:
        edit.user_decision = previous
        raise
    save_artifacts(job_id, res, p1.normalized_transcript)
    return res


def set_name_decision(job_id: str, name_id: str, decision: str) -> RefinementResult:
    res = load_refinement(job_id)
    p1 = load_phase1(job_id)
    n = next((x for x in res.speaker_names if x.name_id == name_id), None)
    if n is None:
        raise PipelineError("NAME_NOT_FOUND", f"No speaker-name proposal '{name_id}'.")
    if decision not in ("accept", "reject", "reset"):
        raise PipelineError("BAD_DECISION", "Decision must be accept, reject or reset.")
    if decision == "accept" and n.status == "rejected":
        raise PipelineError("EDIT_REJECTED", f"This proposal is blocked and cannot be accepted: {n.reject_reason}")
    n.user_decision = None if decision == "reset" else decision  # type: ignore[assignment]
    _recompute(res, p1)
    save_artifacts(job_id, res, p1.normalized_transcript)
    return res


def set_manual_name(job_id: str, speaker: str, name: str) -> RefinementResult:
    res = load_refinement(job_id)
    p1 = load_phase1(job_id)
    labels = {s.speaker for s in res.refined_transcript.segments if s.speaker_known}
    if speaker not in labels:
        raise PipelineError("SPEAKER_NOT_FOUND", f"No speaker '{speaker}' in this transcript.")
    name = " ".join(name.split())
    if len(name) > 40 or any(ord(c) < 32 for c in name):
        raise PipelineError("BAD_NAME", "Names must be at most 40 characters.")
    if name:
        res.manual_names[speaker] = name
    else:
        res.manual_names.pop(speaker, None)
    _recompute(res, p1)
    save_artifacts(job_id, res, p1.normalized_transcript)
    return res


def _stats_from(res: RefinementResult, p1: ProcessingResult) -> dict[str, int]:
    s = _stats(res.edits, [None] * res.stats.get("proposed", 0), res.refined_transcript, p1.normalized_transcript)
    s["applied"] = sum(1 for e in res.edits if is_applied(e))
    s["user_overrides"] = sum(1 for e in res.edits if e.user_decision)
    s["speakers_named"] = len(effective_names(res.speaker_names, res.manual_names))
    return s


def save_artifacts(job_id: str, res: RefinementResult, normalized) -> None:
    d = job_dir(job_id)
    (d / "refinement.json").write_text(res.model_dump_json(indent=2))
    (d / "refined_transcript.json").write_text(res.refined_transcript.model_dump_json(indent=2))
    (d / "refined_transcript.txt").write_text(
        "\n".join(f"[{s.start:7.2f}-{s.end:7.2f}] {s.speaker_name + ' (' + s.speaker + ')' if s.speaker_name else s.speaker}: {s.text}"
                  for s in res.refined_transcript.segments))
    (d / "speakers.json").write_text(json.dumps({
        "source": res.speaker_source, "names": effective_names(res.speaker_names, res.manual_names),
        "proposals": [n.model_dump() for n in res.speaker_names], "manual": res.manual_names,
        "segments": {k: v.model_dump() for k, v in res.segment_speakers.items()}}, indent=2, ensure_ascii=False))
    (d / "edits.json").write_text(json.dumps([e.model_dump() for e in res.edits], indent=2, ensure_ascii=False))
    with open(d / "edits.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edit_id", "segment_id", "start", "kind", "find", "replace", "status", "applied_now", "user_decision", "reject_reason", "evidence", "reason"])
        for e in res.edits:
            w.writerow([e.edit_id, e.segment_id, e.start, e.kind, e.find, e.replace, e.status, is_applied(e), e.user_decision or "",
                        e.reject_reason or "", "; ".join(x.detail for x in e.support), e.reason])
    lines = []
    refined_by_id = {s.segment_id: s for s in res.refined_transcript.segments}
    for s in normalized.segments:
        r = refined_by_id[s.segment_id]
        if r.text == s.text:
            continue
        lines += [f"[{s.start:.1f}-{s.end:.1f}s] {s.speaker} {s.segment_id}", f"- {s.text}", f"+ {r.text}"]
        for e in res.edits:
            if e.segment_id == s.segment_id and is_applied(e):
                lines.append(f"    {e.edit_id} {e.kind}: '{e.find}' -> '{e.replace}'  ({'; '.join(x.detail for x in e.support) or 'no evidence'})")
        lines.append("")
    (d / "diff.txt").write_text("\n".join(lines) if lines else "No changes.\n")
