"""Readable (Markdown) and machine-readable (JSON, CSV) outputs, all built from the same Result."""
import csv
import io
import json
import re
from pathlib import Path

from app.utils.atomic import atomic_write
from app.core.lenses import LENS_ORDER, LENSES
from app.core.schemas import Record, Result

PLURAL = {"decision": "decisions", "action": "action_items", "question": "open_questions", "proposal": "proposals",
          "disagreement": "disagreements", "risk": "risks", "fact": "key_figures", "point": "discussion_points"}
SCHEMA_VERSION = "meeting-record/1"
BADGE = {"code": "checked against the recording (exact quotes, numbers)", "code+model": "checked against the recording and by a second, independent check", "needs_review": "NEEDS REVIEW: the second check doubted it"}


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def _clean(s) -> str:
    return " ".join(str(s).split())


def names(r: Result) -> dict[str, str]:
    return {n.speaker: n.name for n in r.speaker_names}


def label_name(label: str) -> str:
    """SPEAKER_00 -> Speaker 1, SPEAKER_B -> Speaker B."""
    if label == "SPEAKER_UNKNOWN":
        return "unknown speaker"
    m = re.fullmatch(r"SPEAKER_(\d+)", label)
    if m:
        return f"Speaker {int(m[1]) + 1}"
    m = re.fullmatch(r"SPEAKER_([A-Z])", label)
    return f"Speaker {m[1]}" if m else label


def who(label: str, nm: dict[str, str]) -> str:
    return nm.get(label) or label_name(label)


def owner_of(rec: Record, nm: dict[str, str]) -> str:
    o = rec.attrs["owner"]
    return who(o["speaker"], nm) if o["basis"] == "first_person" and o["speaker"] else (o["text"] or "unspecified")


def registers(r: Result) -> dict[str, list[Record]]:
    return {PLURAL[k]: [x for x in r.records if x.kind == k] for k in LENS_ORDER}


def _evidence(rec: Record, nm: dict[str, str]) -> dict:
    e = rec.evidence[0]
    return {"quote": _clean(e.quote), "speaker": who(e.speaker, nm), "time": mmss(e.start), "start_seconds": round(e.start, 2)}


def decisions_export(r: Result) -> list[dict]:
    """Key decisions; an empty list if none were reached."""
    nm = names(r)
    return [{"id": x.rid, "decision": _clean(x.text), "context": _clean(r.decision_context[x.rid].text) if x.rid in r.decision_context else None, "status": x.status, "checked_by": x.check, "evidence": _evidence(x, nm)} for x in r.records if x.kind == "decision"]


def actions_export(r: Result) -> list[dict]:
    """Action items; owner and deadline are the literal word "unspecified" when the meeting did not state them. An empty list if none were assigned."""
    nm = names(r)
    return [{"id": x.rid, "task": _clean(x.text), "status": x.status, "owner": owner_of(x, nm), "owner_basis": x.attrs["owner"]["basis"],
             "deadline": x.attrs.get("deadline") or "unspecified", "evidence": _evidence(x, nm)} for x in r.records if x.kind == "action"]


def minutes_export(r: Result) -> dict:
    nm = names(r)
    return {"headline": _clean(r.headline.text) if r.headline else None, "summary": [{"text": _clean(c.text), "records": c.record_ids} for c in r.summary],
            "topics": [{"title": t.title, "outcome": t.outcome, "from": mmss(t.start), "to": mmss(t.end), "points": [{"text": _clean(b.text), "records": b.record_ids} for b in t.bullets]} for t in r.topics],
            "open_questions": [{"id": x.rid, "question": _clean(x.text), "evidence": _evidence(x, nm)} for x in r.records if x.kind == "question" and x.status == "open"],
            "key_figures": [{"id": x.rid, "label": _clean(str(x.attrs.get("label", ""))), "value": _clean(str(x.attrs.get("value", ""))), "evidence": _evidence(x, nm)} for x in r.records if x.kind == "fact"]}


def to_export(r: Result, with_calls: bool = False) -> dict:
    d = r.model_dump(exclude=set() if with_calls else {"calls"})
    if not with_calls:
        d["calls"] = [{k: c[k] for k in ("n", "stage", "model", "effort", "window", "prompt_tokens", "completion_tokens", "secs", "cache_hit", "error")} for c in r.model_dump()["calls"]]
    d["schema_version"] = SCHEMA_VERSION
    d["registers"] = {k: [x.model_dump() for x in v] for k, v in registers(r).items()}
    # The five required outputs, named plainly. "transcript" is the speech-to-text result before refinement, "refined" the corrected one.
    d["minutes"] = minutes_export(r)
    d["decisions"] = decisions_export(r)
    d["action_items"] = actions_export(r)
    return d


def _rec_md(rec: Record, nm: dict[str, str], ctx: dict | None = None) -> str:
    e = rec.evidence[0]
    head = f"- **{rec.rid}** [{rec.status}] {_clean(rec.text)}"
    if rec.kind == "action":
        head += f"\n  - Owner: {owner_of(rec, nm)} | Deadline: {rec.attrs.get('deadline') or 'unspecified'}"
    if rec.kind == "fact":
        head = f"- **{rec.rid}** {_clean(rec.attrs['label'])}: **{_clean(rec.attrs['value'])}** ({_clean(rec.text)})"
    return head + (f"\n  - Context: {_clean(ctx[rec.rid].text)}" if ctx and rec.rid in ctx else "") + f"\n  - \"{_clean(e.quote)}\" ({who(e.speaker, nm)}, {mmss(e.start)})\n  - _{BADGE[rec.check]}_" + "".join(f"\n  - {_clean(n)}" for n in rec.notes)


def to_markdown(r: Result) -> str:
    nm = names(r)
    out = ["# Meeting minutes", "",
           f"Recording {mmss(r.audio.duration_seconds)} | {len(r.transcript.segments)} transcript segments | status **{r.status}**", ""]
    if r.status == "partial":
        out += ["> **INCOMPLETE:** some steps did not finish (see Notes). Parts of this record may be missing.", ""]
    if r.headline:
        out += [f"**{_clean(r.headline.text)}**", ""]
    out += ["## Summary", ""] + ([f"- {_clean(c.text)} _[{', '.join(c.record_ids)}]_" for c in r.summary] or ["No verified outcomes were extracted."])
    out += ["", "## Speakers", ""] + ([f"- {n.name} ({label_name(n.speaker)}): \"{_clean(n.quote)}\"" for n in r.speaker_names] or ["No speaker introduced themselves (or speaker labels were not available)."])
    if r.speaker_summaries:
        out += ["", "## What each person said", ""]
        for label, claims in r.speaker_summaries.items():
            out.append(f"- **{who(label, nm)}**")
            out += [f"  - {_clean(c.text)} _[{', '.join(c.record_ids)}]_" for c in claims]
    out += ["", "## Topics", ""]
    for t in r.topics:
        out.append(f"- {t.title} ({mmss(t.start)} to {mmss(t.end)})" + (f" [{t.outcome}]" if t.outcome else ""))
        out += [f"  - {_clean(b.text)} _[{', '.join(b.record_ids)}]_" for b in t.bullets]
    if not r.topics:
        out.append("No topic structure.")
    reg = registers(r)
    for k in LENS_ORDER:
        out += ["", f"## {LENSES[k]['title']}", ""]
        out += [_rec_md(x, nm, r.decision_context) for x in reg[PLURAL[k]]] or ["None recorded in this meeting."]
    from app.core import insights  # same reason

    from app.core import insights  # same reason

    att = insights.attention(r)
    if att:
        out += ["", "## Needs attention", ""] + [f"- {x}" for x in att]
    out += ["", "## Next steps (by owner)", ""] + insights.checklist_md(r).splitlines()[2:]
    out += ["", "## Transcript corrections", ""]
    if r.edits:
        for e in r.edits:
            why = f" ({e.reject_reason})" if e.reject_reason else ""
            out.append(f"- [{e.status}] {mmss(e.start)}: \"{_clean(e.find)}\" -> \"{_clean(e.replace)}\"{why}")
    else:
        out.append("None proposed.")
    from app.core.wording import plain

    out += ["", "## Notes and limitations", ""] + ([f"- {_clean(plain(w))}" for w in r.warnings] or ["- None."])
    return "\n".join(out) + "\n"


def actions_csv(r: Result) -> str:
    nm = names(r)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "task", "status", "owner", "owner_basis", "deadline", "time", "check", "quote"])
    for x in registers(r)["action_items"]:
        w.writerow([x.rid, x.text, x.status, owner_of(x, nm), x.attrs["owner"]["basis"], x.attrs.get("deadline") or "unspecified", mmss(x.start), x.check, x.evidence[0].quote])
    return buf.getvalue()


def transcript_txt(r: Result) -> str:
    return "\n".join(f"[{s.start:7.2f}-{s.end:7.2f}] {s.speaker_name + ' (' + label_name(s.speaker) + ')' if s.speaker_name else label_name(s.speaker)}: {s.text}" for s in r.refined.segments)


def save(out_dir: Path, r: Result, raw_asr: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write((out_dir / "result.json"), r.model_dump_json(indent=1))  # full, including model inputs and outputs
    atomic_write((out_dir / "minutes.json"), json.dumps(to_export(r), indent=1, ensure_ascii=False))
    atomic_write((out_dir / "minutes.md"), to_markdown(r))
    from app.core.report_html import to_html  # imported here: report_html uses helpers from this module

    atomic_write((out_dir / "report.html"), to_html(r))
    from app.core import insights  # same reason

    atomic_write((out_dir / "next_steps.md"), insights.checklist_md(r))
    atomic_write((out_dir / "email.txt"), insights.email_text(r))
    cal, n_events, _ = insights.ics(r)
    if n_events:
        atomic_write((out_dir / "action_items.ics"), cal)
    elif (out_dir / "action_items.ics").exists():
        (out_dir / "action_items.ics").unlink()
    atomic_write((out_dir / "action_items.csv"), actions_csv(r))
    atomic_write((out_dir / "refined_transcript.txt"), transcript_txt(r))  # after refinement, with speaker names
    atomic_write((out_dir / "calls.json"), json.dumps([c.model_dump() for c in r.calls], indent=1, ensure_ascii=False))
    if raw_asr is not None:
        atomic_write((out_dir / "asr_raw.json"), json.dumps(raw_asr, ensure_ascii=False))
