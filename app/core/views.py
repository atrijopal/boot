"""What the page shows, assembled from the saved artifacts. Read-only; nothing here runs a model or changes a file."""
import json
from pathlib import Path

from app.config.settings import settings
from app.core import exports, insights, issues, session
from app.core.corrections import effective_edits, load_overrides
from app.core.pipeline import load_phase1, load_phase2, load_result
from app.core.report import label_name, mmss, names, owner_of, registers, who
from app.utils.errors import PipelineError

KIND_TEXT = {"term": "Technical term corrected", "acronym": "Acronym normalised", "name": "Name spelling made consistent", "spelling": "Spelling corrected", "format": "Formatting corrected"}
BASIS_TEXT = {"self": "Said by the speaker themselves", "addressed": "Said to them, and they answered"}
CHECK_WORD = {"code": "recording", "code+model": "recording_and_second_check", "needs_review": "needs_review"}  # how an item was checked, in product words
SIDE = 70  # characters of context shown on each side of a correction


def _dir(job: str) -> Path:
    return Path(settings.outputs_dir) / job


def _mtime(job: str, name: str) -> float:
    try:
        return (_dir(job) / name).stat().st_mtime
    except OSError:
        return 0.0


# ------------------------------------------------------------------------------------------------ speakers
def speakers_view(job: str) -> dict:
    p1 = load_phase1(job)
    p2 = load_phase2(job)
    segs = (p2.refined if p2 else p1.transcript).segments
    nm = exports.names_map(job)
    typed = {k: v for k, v in load_overrides(job).items() if v}
    sug: dict[str, list] = {}
    for x in (p2.name_suggestions if p2 else []):
        sug.setdefault(x.speaker, []).append({"id": x.suggestion_id, "name": x.name, "role": x.role, "basis": x.basis, "evidence_text": BASIS_TEXT[x.basis], "quote": x.quote, "start": round(x.start, 1),
                                              "status": x.status, "decision": x.user_decision, "in_force": (x.status == "applied" and x.user_decision != "reject") or x.user_decision == "accept"})
    total = sum(max(0.0, s.end - s.start) for s in segs if s.speaker_known) or 1.0
    rows: dict[str, dict] = {}
    for s in segs:
        if not s.speaker_known:
            continue
        r = rows.setdefault(s.speaker, {"label": s.speaker, "display": label_name(s.speaker), "lines": 0, "words": 0, "seconds": 0.0, "first_start": s.start, "occurrences": []})
        r["lines"] += 1
        r["words"] += len(s.text.split())
        r["seconds"] += max(0.0, s.end - s.start)
        if len(r["occurrences"]) < 400:
            r["occurrences"].append({"segment_id": s.segment_id, "start": round(s.start, 1), "text": s.text})
    out = []
    for label, r in sorted(rows.items()):  # Speaker 1, 2, 3 ... in a stable order
        r["seconds"] = round(r["seconds"], 1)
        r["share"] = round(100 * r["seconds"] / total)
        r["name"] = nm.get(label)
        r["name_source"] = "typed" if label in typed else ("meeting" if nm.get(label) else None)
        r["suggestions"] = sug.get(label, [])
        out.append(r)
    return {"diarized": bool(out), "speakers": out, "typed_names": typed}


# ------------------------------------------------------------------------------------------------ refinement
def refinement_view(job: str) -> dict:
    p1 = load_phase1(job)
    p2 = load_phase2(job)
    if p2 is None:
        return {"ready": False, "changes": [], "counts": {}}
    raw = {s.segment_id: s for s in p1.transcript.segments}
    eff = effective_edits(p2)
    changes = []
    for e, ee in zip(p2.edits, eff):
        seg = raw.get(e.segment_id)
        text = seg.text if seg else ""
        cs, ce = e.char_start, e.char_end
        before = text[max(0, (cs or 0) - SIDE) : cs] if cs is not None else ""
        after = text[ce : ce + SIDE] if ce is not None else ""
        in_force = ee.status == "applied"
        state = "rejected" if (e.status == "rejected" or e.user_decision == "reject") else ("accepted" if in_force else "needs_review")
        changes.append({"id": e.edit_id, "segment_id": e.segment_id, "start": round(e.start, 1), "original": e.find, "proposed": e.replace, "before": ("…" if cs and cs > SIDE else "") + before,
                        "after": after + ("…" if ce is not None and ce + SIDE < len(text) else ""), "kind": e.kind, "reason": KIND_TEXT.get(e.kind, "Correction"), "similarity": e.similarity,
                        "evidence": [x.detail for x in e.support], "blocked": e.status == "rejected", "blocked_because": e.reject_reason, "decision": e.user_decision, "in_force": in_force, "state": state,
                        "automatic": e.status})
    counts = {"proposed": len(changes), "needs_review": sum(c["state"] == "needs_review" for c in changes), "accepted": sum(c["state"] == "accepted" for c in changes),
              "rejected": sum(c["state"] == "rejected" for c in changes)}
    return {"ready": True, "changes": changes, "counts": counts}


# ------------------------------------------------------------------------------------------------ report
def report_view(job: str) -> dict:
    try:
        r = load_result(job)
    except PipelineError:
        return {"ready": False}
    nm = names(r)
    reg = registers(r)

    def ev(x):
        e = x.evidence[0]
        return {"quote": " ".join(e.quote.split()), "speaker": who(e.speaker, nm), "time": mmss(e.start), "start": round(e.start, 1), "segment_id": e.segment_ids[0] if e.segment_ids else None}

    decisions = [{"id": x.rid, "text": " ".join(x.text.split()), "status": x.status, "context": r.decision_context[x.rid].text if x.rid in r.decision_context else None, "checked": CHECK_WORD[x.check], "unconfirmed": not r.review_ran, "evidence": ev(x)} for x in reg["decisions"]]
    actions = []
    for x in reg["action_items"]:
        own, dl = owner_of(x, nm), x.attrs.get("deadline")
        actions.append({"id": x.rid, "task": " ".join(x.text.split()), "status": x.status, "owner": None if own == "unspecified" else own, "owner_basis": x.attrs["owner"]["basis"], "deadline": dl or None,
                        "evidence": ev(x), "checked": CHECK_WORD[x.check]})
    return {
        "ready": True,
        "final_check": r.review_ran,
        "headline": r.headline.text if r.headline else None,
        "summary": [{"text": c.text, "records": c.record_ids} for c in r.summary],
        "topics": [{"title": t.title, "start": round(t.start), "end": round(t.end), "outcome": t.outcome, "points": [b.text for b in t.bullets]} for t in r.topics],
        "decisions": decisions,
        "action_items": actions,
        "proposals": [{"id": x.rid, "text": x.text, "status": x.status, "evidence": ev(x)} for x in reg["proposals"]],
        "open_questions": [{"id": x.rid, "text": x.text, "evidence": ev(x)} for x in reg["open_questions"] if x.status == "open"],
        "risks": [{"id": x.rid, "text": x.text, "evidence": ev(x)} for x in reg["risks"]],
        "key_figures": [{"id": x.rid, "label": x.attrs.get("label"), "value": x.attrs.get("value"), "evidence": ev(x)} for x in reg["key_figures"]],
        "discussion_points": [{"id": x.rid, "text": x.text, "evidence": ev(x)} for x in reg["discussion_points"]],
        "speakers": [{"label": a["speaker"], "name": a["name"], "role": a["role"], "share": a["share"], "summary": [c.text for c in a["summary"]]} for a in insights.speaker_summary(r)],
        "attention": insights.attention(r),
        "speakers_inferred": r.speakers_inferred,
        "duration": r.audio.duration_seconds,
    }


# ------------------------------------------------------------------------------------------------ the whole meeting
def freshness(job: str) -> dict:
    """Has anything the report depends on changed since it was made?"""
    made = _mtime(job, "result.json")
    if not made:
        return {"report_stale": False, "reason": ""}
    changed = max(_mtime(job, "phase2.json"), _mtime(job, "overrides.json"))
    stale = changed > made + 1
    return {"report_stale": stale, "reason": "Corrections or speaker names changed after this report was generated. Regenerate it to include them." if stale else ""}


def _final_check(job: str) -> bool | None:
    """Did the final check (review of decisions and tasks) run for the current report? None when there is no report."""
    try:
        return load_result(job).review_ran
    except PipelineError:
        return None


def meeting_view(job: str) -> dict:
    s = session.load(job) or session.derive(job)
    return {"session": s, "final_check": _final_check(job), "issues": issues.collect(job), "exports": [{k: v for k, v in e.items() if k in ("key", "ready")} for e in exports.catalog(job)], **freshness(job)}


# ------------------------------------------------------------------------------------------------ pipeline details (audit)
def _clip(text: str, n: int = 6000) -> str:
    return text if len(text) <= n else text[:n] + f"\n… ({len(text) - n} more characters; download the full file from Export)"


def pipeline_view(job: str, developer: bool = False) -> dict:
    s = session.load(job) or session.derive(job)
    all_issues = issues.collect(job)
    p1 = None
    try:
        p1 = load_phase1(job)
    except PipelineError:
        pass
    p2 = load_phase2(job)
    out = []
    for key in session.STAGES:
        st = s["stages"][key]
        item = {"key": key, "label": session.LABELS[key], "state": st["state"], "seconds": st["seconds"], "message": st["message"], "error": st["error"], "attempts": st["attempts"],
                "issues": [i for i in all_issues if i["stage"] == key], "input": "", "output": None}
        if key == "audio":
            a = s.get("audio") or {}
            item["input"] = f"Uploaded file: {s.get('filename')}"
            item["output"] = {"kind": "json", "title": "Audio check", "content": json.dumps(a, indent=1)} if a else None
        elif key == "transcription" and p1:
            item["input"] = f"Audio, {mmss(p1.audio.duration_seconds)}"
            item["output"] = {"kind": "text", "title": "Raw transcript, exactly as produced", "content": _clip(exports._txt(exports.raw_segments(job)))}
        elif key == "speakers" and p2:
            v = speakers_view(job)
            item["input"] = f"Raw transcript, {len(p1.transcript.segments) if p1 else 0} lines"
            item["output"] = {"kind": "json", "title": "Speakers and suggested names",
                              "content": json.dumps([{k: x[k] for k in ("display", "name", "lines", "share", "suggestions")} for x in v["speakers"]], indent=1, ensure_ascii=False)}
        elif key == "refinement" and p2:
            v = refinement_view(job)
            item["input"] = f"Raw transcript with speakers, {len(p2.refined.segments)} lines"
            item["output"] = {"kind": "json", "title": "Proposed corrections", "content": json.dumps([{k: c[k] for k in ("id", "start", "original", "proposed", "reason", "state", "similarity", "evidence")} for c in v["changes"]], indent=1, ensure_ascii=False)}
        elif key == "report" and st["state"] == "completed":
            v = report_view(job)
            item["input"] = f"Refined transcript, {len(p2.refined.segments) if p2 else 0} lines"
            item["output"] = {"kind": "json", "title": "Structured meeting record", "content": _clip(json.dumps({k: v[k] for k in ("headline", "decisions", "action_items", "open_questions")}, indent=1, ensure_ascii=False), 8000)}
        out.append(item)
    res = {"stages": out, "events": s.get("events", [])[-60:]}
    if developer:
        calls = []
        try:
            calls = load_result(job).calls
        except PipelineError:
            calls = p2.calls if p2 else []
        res["developer"] = [{"n": c.n, "stage": c.stage, "window": c.window, "model": c.model, "effort": c.effort, "seconds": c.secs, "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens,
                             "fallback_for": c.fallback_for, "effort_fallback": c.effort_fallback, "error": c.error, "output": c.output} for c in calls]
    return res
