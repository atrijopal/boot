"""Everything a meeting can be exported as, with professional file names, and whether each one really exists yet.

An artifact is offered only once the stage that produces it has completed, so a button never leads to a missing or half-made file.
"""
import io
import json
import zipfile

from app.core import insights, pdf, report, session
from app.core.corrections import effective_edits, load_overrides, merge_names
from app.core.pipeline import load_phase1, load_phase2, load_result
from app.core.report import label_name, mmss
from app.utils.errors import PipelineError

# key -> (label, group, extension, mime, stage that must be done)
CATALOG = {
    "raw_transcript_txt": ("Raw transcript", "Raw transcript", "txt", "text/plain", "transcription"),
    "raw_transcript_json": ("Raw transcript", "Raw transcript", "json", "application/json", "transcription"),
    "refined_transcript_txt": ("Refined transcript", "Refined transcript", "txt", "text/plain", "refinement"),
    "refined_transcript_json": ("Refined transcript", "Refined transcript", "json", "application/json", "refinement"),
    "report_pdf": ("Meeting report", "Meeting report", "pdf", "application/pdf", "report"),
    "report_md": ("Meeting report", "Meeting report", "md", "text/markdown", "report"),
    "report_html": ("Meeting report", "Meeting report", "html", "text/html", "report"),
    "record_json": ("Structured record", "Structured record", "json", "application/json", "report"),
    "actions_csv": ("Action items", "More", "csv", "text/csv", "report"),
    "actions_ics": ("Deadlines as calendar events", "More", "ics", "text/calendar", "report"),
    "email_txt": ("Summary for email", "More", "txt", "text/plain", "report"),
    "package_zip": ("Complete package", "Package", "zip", "application/zip", "transcription"),
}
STEM = {"raw_transcript_txt": "raw_transcript", "raw_transcript_json": "raw_transcript", "refined_transcript_txt": "refined_transcript", "refined_transcript_json": "refined_transcript",
        "report_pdf": "report", "report_md": "report", "report_html": "report", "record_json": "record", "actions_csv": "action_items", "actions_ics": "action_items",
        "email_txt": "email_summary", "package_zip": "package"}
WHEN = {"transcription": "Available once the recording has been transcribed.", "refinement": "Available once the transcript has been checked for corrections.",
        "report": "Available once the meeting report has been generated."}


def date_of(s: dict) -> str:
    return s.get("meeting_date") or (s.get("created_at") or "")[:10] or "undated"


def filename(s: dict, key: str) -> str:
    return f"meeting_{date_of(s)}_{STEM[key]}.{CATALOG[key][2]}"


def names_map(job: str) -> dict[str, str]:
    """Speaker label -> name, as confirmed so far (typed names first, then names the meeting itself shows)."""
    try:
        r = load_result(job)
        return {n.speaker: n.name for n in r.speaker_names}
    except PipelineError:
        pass
    p2 = load_phase2(job)
    merged = {n.speaker: n.name for n in (merge_names(p2, [])[0] if p2 else [])}
    for k, v in load_overrides(job).items():
        if v:
            merged[k] = v
        else:
            merged.pop(k, None)
    return merged


def _seg(s, nm: dict[str, str]) -> dict:
    return {"id": s.segment_id, "start": round(s.start, 2), "end": round(s.end, 2), "speaker": label_name(s.speaker) if s.speaker_known else None,
            "speaker_label": s.speaker if s.speaker_known else None, "speaker_name": nm.get(s.speaker) if s.speaker_known else None, "text": s.text}


def raw_segments(job: str) -> list[dict]:
    nm = names_map(job)
    return [_seg(s, nm) for s in load_phase1(job).transcript.segments]


def refined_segments(job: str) -> list[dict]:
    p2 = load_phase2(job)
    if p2 is None:
        return []
    nm = names_map(job)
    applied: dict[str, list] = {}
    for e in effective_edits(p2):
        if e.status == "applied":
            applied.setdefault(e.segment_id, []).append({"original": e.find, "corrected": e.replace, "kind": e.kind})
    return [{**_seg(s, nm), "changes": applied.get(s.segment_id, [])} for s in p2.refined.segments]


def corrections_in_force(job: str) -> list[dict]:
    p2 = load_phase2(job)
    if p2 is None:
        return []
    return [{"time": mmss(e.start), "original": e.find, "corrected": e.replace} for e in effective_edits(p2) if e.status == "applied"]


def _txt(segs: list[dict]) -> str:
    out = []
    for s in segs:
        who = (f"{s['speaker']} ({s['speaker_name']})" if s["speaker_name"] else s["speaker"]) if s["speaker"] else "Unknown speaker"
        out.append(f"[{mmss(s['start'])}] {who}: {s['text']}")
    return "\n".join(out) + "\n"


def _txt_doc(job: str, s: dict, kind: str) -> str:
    """A transcript file that says what it is: title, meeting, speakers, what has (not) been done to it, then the lines. The refined one ends with every correction made."""
    raw = kind == "raw"
    segs = raw_segments(job) if raw else refined_segments(job)
    m = _meeting_block(job, s)
    speakers = []
    for x in segs:
        who = (f"{x['speaker']} ({x['speaker_name']})" if x["speaker_name"] else x["speaker"]) if x["speaker"] else None
        if who and who not in speakers:
            speakers.append(who)
    fixes = [] if raw else corrections_in_force(job)
    head = ["RAW TRANSCRIPT" if raw else "REFINED TRANSCRIPT", f"File: {m['source_file'] or 'Recording'}", f"Date: {m['date'] if m['date_provided'] else 'not provided'}", f"Duration: {mmss(m['duration_seconds'] or 0)}",
            f"Speakers: {', '.join(speakers) if speakers else 'not distinguished in this recording'}"]
    head.append("The speech-to-text result, before any refinement. It has not been edited." if raw else
                f"The raw transcript after terminology, name and acronym corrections ({len(fixes)} applied; listed at the end). The raw transcript is kept unchanged in its own file.")
    body = _txt(segs)
    tail = "" if raw else "\n" + "-" * 60 + "\nCORRECTIONS APPLIED\n" + ("\n".join(f'[{c["time"]}] "{c["original"]}" -> "{c["corrected"]}"' for c in fixes) if fixes else "None were needed.") + "\n"
    return "\n".join(head) + "\n" + "-" * 60 + "\n" + body + tail


def _meeting_block(job: str, s: dict) -> dict:
    return {"id": job, "source_file": s.get("filename"), "date": date_of(s), "date_provided": bool(s.get("meeting_date")), "duration_seconds": (s.get("audio") or {}).get("duration_seconds")}


def _ready(job: str, s: dict, key: str) -> tuple[bool, str]:
    need = CATALOG[key][4]
    if need == "transcription":
        return (s["stages"]["transcription"]["state"] == "completed", WHEN["transcription"])
    if need == "refinement":
        return (s["stages"]["refinement"]["state"] in ("needs_review", "completed"), WHEN["refinement"])
    if s["stages"]["report"]["state"] != "completed":
        return (False, WHEN["report"])
    if key == "actions_ics":
        try:
            if insights.ics(load_result(job))[1] == 0:
                return (False, "No action item has a deadline that can be turned into a calendar date.")
        except PipelineError:
            return (False, WHEN["report"])
    return (True, "")


def catalog(job: str) -> list[dict]:
    s = session.load(job) or session.derive(job)
    out = []
    for key, (label, group, ext, mime, _) in CATALOG.items():
        ok, why = _ready(job, s, key)
        out.append({"key": key, "label": label, "group": group, "ext": ext.upper(), "filename": filename(s, key), "mime": mime, "ready": ok, "reason": "" if ok else why})
    return out


def _build_one(job: str, s: dict, key: str) -> bytes:
    if key == "raw_transcript_txt":
        return _txt_doc(job, s, "raw").encode()
    if key == "refined_transcript_txt":
        return _txt_doc(job, s, "refined").encode()
    if key == "raw_transcript_json":
        p1 = load_phase1(job)
        words = {sg.segment_id: [{"text": w.text, "start": w.start, "end": w.end, "confidence": w.confidence} for w in sg.words] for sg in p1.transcript.segments}
        segs = [{**x, "words": words.get(x["id"], [])} for x in raw_segments(job)]
        return json.dumps({"meeting": _meeting_block(job, s), "kind": "raw_transcript", "note": "The speech-to-text result before any refinement; never modified.", "segments": segs}, indent=1, ensure_ascii=False).encode()
    if key == "refined_transcript_json":
        p2 = load_phase2(job)
        props = [{"id": e.edit_id, "time": mmss(e.start), "segment_id": e.segment_id, "original": e.find, "proposed": e.replace, "kind": e.kind, "automatic_status": e.status,
                  "decision": e.user_decision, "in_force": ee.status == "applied", "similarity": e.similarity, "evidence": [x.detail for x in e.support], "blocked_because": e.reject_reason}
                 for e, ee in zip(p2.edits, effective_edits(p2))]
        return json.dumps({"meeting": _meeting_block(job, s), "kind": "refined_transcript", "note": "The raw transcript after terminology, name and acronym corrections; the raw transcript is a separate file and is never modified.", "segments": refined_segments(job), "proposed_corrections": props}, indent=1, ensure_ascii=False).encode()
    r = load_result(job)
    if key == "report_pdf":
        return pdf.build(r, s, corrections_in_force(job))
    if key in ("report_md", "report_html", "record_json", "actions_csv"):
        from app.core import record

        rec = record.build(job, s)
        if key == "report_md":
            return record.to_markdown(rec, s.get("filename") or "").encode()
        if key == "report_html":
            return record.to_html(rec, s.get("filename") or "").encode()
        if key == "actions_csv":
            return record.actions_csv(rec).encode()
        return json.dumps(rec, indent=1, ensure_ascii=False).encode()
    if key == "actions_ics":
        return insights.ics(r)[0].encode()
    if key == "email_txt":
        return insights.email_text(r).encode()
    raise PipelineError("BAD_EXPORT", f"Unknown export '{key}'.")


DELIVERABLE = {"raw_transcript": "Raw transcript: the speech-to-text result before any refinement", "refined_transcript": "Refined transcript: after terminology correction, with every change listed",
               "report": "Meeting minutes and key decisions and action items, as a document", "record": "Meeting record in machine-readable form: raw and refined transcripts, minutes, decisions, action items",
               "action_items": "Action items (owner and deadline are the word unspecified when the meeting did not state them)", "email_summary": "A short summary ready to paste into an email", "package": ""}


def _manifest(s: dict, made: list[str]) -> str:
    lines = ["MEETING PACKAGE", f"File: {s.get('filename') or 'Recording'}", f"Date: {date_of(s)}", "", "Files in this package:"]
    lines += [f"- {filename(s, k)}: {DELIVERABLE[STEM[k]]}" for k in made]
    lines += ["", "The human-readable report (PDF, Markdown, HTML) and the structured record (JSON) contain the same decisions and action items. A missing owner or deadline is written as unspecified; nothing is guessed."]
    return "\n".join(lines) + "\n"


def build(job: str, key: str) -> tuple[bytes, str, str]:
    """(bytes, file name, mime) for one export; refuses one that is not ready yet."""
    if key not in CATALOG:
        raise PipelineError("BAD_EXPORT", f"Unknown export '{key}'.")
    s = session.load(job) or session.derive(job)
    ok, why = _ready(job, s, key)
    if not ok:
        raise PipelineError("EXPORT_NOT_READY", why)
    if key == "package_zip":
        buf = io.BytesIO()
        folder = f"meeting_{date_of(s)}"
        made: list[str] = []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for k in CATALOG:
                if k != "package_zip" and _ready(job, s, k)[0]:
                    z.writestr(f"{folder}/{filename(s, k)}", _build_one(job, s, k))
                    made.append(k)
            z.writestr(f"{folder}/README.txt", _manifest(s, made))
        return buf.getvalue(), filename(s, key), CATALOG[key][3]
    return _build_one(job, s, key), filename(s, key), CATALOG[key][3]
