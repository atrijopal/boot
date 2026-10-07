"""Changes the user makes after the model has run: accept/reject a transcript fix, rename a speaker.

Neither needs a model call. A fix the code BLOCKED (it touched a number, date, time or negation, or was not verbatim) can never be accepted.
"""
import json
import re
from pathlib import Path

from app.config.settings import settings
from app.core import report, speakers
from app.core.gates import apply_edits
from app.core.pipeline import _job_dir, _txt, load_phase1, load_phase2, load_result
from app.core.schemas import Phase2, Result, SpeakerName
from app.schemas.refinement import Edit
from app.utils.atomic import atomic_write
from app.utils.errors import PipelineError

NAME_OK = re.compile(r"^[^\W\d_][\w'’\-\. ]{0,39}$")


def effective_edits(p2: Phase2) -> list[Edit]:
    """The edits as the user left them: applied unless rejected by the user; suggested only if accepted by the user; blocked stays blocked."""
    out = []
    for e in p2.edits:
        on = e.status != "rejected" and ((e.status == "applied" and e.user_decision != "reject") or (e.status == "suggested" and e.user_decision == "accept"))
        out.append(e.model_copy(update={"status": "applied" if on else ("rejected" if e.status == "rejected" else "suggested")}))
    return out


def _base(job_id: str, p2: Phase2):
    t = load_phase1(job_id).transcript
    return speakers.apply(t, p2.speaker_labels) if p2.speaker_labels else t


def decide(job_id: str, edit_id: str, decision: str) -> Phase2:
    """decision: accept | reject | reset. Rebuilds the refined transcript and saves phase 2 again."""
    if decision not in ("accept", "reject", "reset"):
        raise PipelineError("BAD_DECISION", "decision must be accept, reject or reset.")
    p2 = load_phase2(job_id)
    if p2 is None:
        raise PipelineError("PHASE2_MISSING", "Run phase 2 first.")
    e = next((x for x in p2.edits if x.edit_id == edit_id), None)
    if e is None:
        raise PipelineError("EDIT_NOT_FOUND", "No such fix in this job.")
    if e.status == "rejected" and decision == "accept":
        raise PipelineError("EDIT_BLOCKED", f"This fix is blocked and cannot be accepted: {e.reject_reason or 'it broke a safety rule'}.")
    e.user_decision = None if decision == "reset" else decision
    p2.refined = apply_edits(_base(job_id, p2), effective_edits(p2), {})
    d = _job_dir(job_id)
    atomic_write((d / "phase2.json"), p2.model_dump_json(indent=1))
    atomic_write((d / "refined_transcript.txt"), _txt(p2.refined))
    return p2


# ------------------------------------------------------------------------------------------------ speaker names
def load_overrides(job_id: str) -> dict[str, str]:
    f = _job_dir(job_id) / "overrides.json"
    try:
        return {str(k): str(v) for k, v in json.loads(f.read_text(encoding="utf-8")).get("names", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def effective_names(p2: Phase2) -> tuple[list[SpeakerName], dict[str, str]]:
    """Names and roles from phase 2 as the user left them: self-introductions stay unless rejected; names said to someone only if accepted."""
    names, roles = [], {}
    for x in p2.name_suggestions:
        if not ((x.status == "applied" and x.user_decision != "reject") or (x.status == "suggested" and x.user_decision == "accept")):
            continue
        if x.name:
            names.append(SpeakerName(speaker=x.speaker, name=x.name, uid=x.uid, quote=x.quote, basis=x.basis, role=x.role))
        if x.role:
            roles.setdefault(x.speaker, x.role)
    return names, roles


def merge_names(p2: Phase2 | None, extracted: list[SpeakerName]) -> tuple[list[SpeakerName], dict[str, str]]:
    """Phase-2 names first, then names the extraction step found; one name per speaker, one speaker per name."""
    first, roles = effective_names(p2) if p2 else ([], {})
    out, labels, used = [], set(), set()
    for n in first + [x for x in extracted if x.basis != "addressed"]:  # a name only said to someone is evidence for a suggestion, not a fact to print in the report
        if n.speaker in labels or n.name.lower() in used:
            continue
        out.append(n)
        labels.add(n.speaker)
        used.add(n.name.lower())
    return out, roles


def set_names(r: Result, names: list[SpeakerName], roles: dict[str, str], typed: dict[str, str]) -> Result:
    """Typed names replace the model's for the same label (an empty typed name removes it). Updates the transcript's speaker names too."""
    kept = [n for n in names if n.speaker not in typed]
    all_names = kept + [SpeakerName(speaker=k, name=v, uid="", quote="(typed by you)", basis="edited") for k, v in typed.items() if v]
    by_label = {n.speaker: n.name for n in all_names}
    refined = r.refined.model_copy(update={"segments": [s.model_copy(update={"speaker_name": by_label.get(s.speaker) if s.speaker_known else None}) for s in r.refined.segments]})
    return r.model_copy(update={"speaker_names": all_names, "speaker_roles": roles, "refined": refined})


def rebuild_names(job_id: str) -> None:
    """After a name decision or a typed name: recompute the final names from the saved files and rewrite the report and downloads (no model call)."""
    d = _job_dir(job_id)
    if not (d / "result.json").is_file():
        return
    r = load_result(job_id)
    extracted = r.extracted_names if r.extracted_names is not None else [n for n in r.speaker_names if n.basis != "edited"]  # older results: treat what was saved as extracted
    names, roles = merge_names(load_phase2(job_id), extracted)
    report.save(d, set_names(r.model_copy(update={"extracted_names": extracted}), names, roles, load_overrides(job_id)))


def decide_name(job_id: str, suggestion_id: str, decision: str) -> Phase2:
    """accept | reject | reset one name or role suggestion from phase 2."""
    if decision not in ("accept", "reject", "reset"):
        raise PipelineError("BAD_DECISION", "decision must be accept, reject or reset.")
    p2 = load_phase2(job_id)
    if p2 is None:
        raise PipelineError("PHASE2_MISSING", "Run phase 2 first.")
    x = next((n for n in p2.name_suggestions if n.suggestion_id == suggestion_id), None)
    if x is None:
        raise PipelineError("SUGGESTION_NOT_FOUND", "No such suggestion in this job.")
    x.user_decision = None if decision == "reset" else decision
    atomic_write((_job_dir(job_id) / "phase2.json"), p2.model_dump_json(indent=1))
    rebuild_names(job_id)
    return p2


def rename_speakers(job_id: str, names: dict[str, str]) -> None:
    """Saves the typed names and, if phase 3 has run, rebuilds the report and downloads from the saved result (no model call)."""
    p1 = load_phase1(job_id)
    p2 = load_phase2(job_id)
    labels = {s.speaker for s in (p2.refined if p2 else p1.transcript).segments if s.speaker_known}
    clean: dict[str, str] = {}
    for label, name in names.items():
        name = " ".join(str(name).split())
        if label not in labels:
            raise PipelineError("UNKNOWN_SPEAKER", f"'{label}' is not a speaker in this meeting. Known: {', '.join(sorted(labels)) or 'none'}.")
        if name and not NAME_OK.match(name):
            raise PipelineError("BAD_NAME", "A name must be 1 to 40 letters (spaces, hyphens and apostrophes are fine).")
        clean[label] = name
    merged = {**load_overrides(job_id), **clean}
    merged = {k: v for k, v in merged.items() if v or k in clean}  # keep an empty name that was just typed: it hides the model's guess
    typed = [v.lower() for v in merged.values() if v]
    if len(typed) != len(set(typed)):
        raise PipelineError("DUPLICATE_NAME", "Two speakers cannot have the same name.")
    atomic_write((_job_dir(job_id) / "overrides.json"), json.dumps({"names": merged}, indent=1))
    rebuild_names(job_id)


def decide_all(job_id: str, action: str) -> Phase2:
    """accept_suggested | reject_suggested | reset: apply one decision to every correction still waiting for the user (or clear all decisions)."""
    if action not in ("accept_suggested", "reject_suggested", "reset"):
        raise PipelineError("BAD_DECISION", "action must be accept_suggested, reject_suggested or reset.")
    p2 = load_phase2(job_id)
    if p2 is None:
        raise PipelineError("PHASE2_MISSING", "Run phase 2 first.")
    for e in p2.edits:
        if action == "reset":
            e.user_decision = None
        elif e.status == "suggested" and e.user_decision is None:
            e.user_decision = "accept" if action == "accept_suggested" else "reject"
    p2.refined = apply_edits(_base(job_id, p2), effective_edits(p2), {})
    d = _job_dir(job_id)
    atomic_write((d / "phase2.json"), p2.model_dump_json(indent=1))
    atomic_write((d / "refined_transcript.txt"), _txt(p2.refined))
    return p2
