"""The meeting record as one structure, and its human-readable forms.

The page, the Markdown, the HTML, the CSV and the JSON all come from `build()`, so a decision or task is the same everywhere. The five outputs the brief asks for are
in the JSON under their own names: `raw_transcript`, `refined_transcript`, `minutes`, `decisions`, `action_items`. A missing owner or deadline is the word "unspecified"."""
import csv
import html
import io

from app.core import exports, views
from app.core.report import label_name, mmss

UNSPEC = "unspecified"
SCHEMA = "meeting-record/1"
NOT_RUN = "The final check did not run: decisions and action items are matched to the recording but have not been double-checked."
FOOT = ("Every item in this record is taken from the recording; the source gives the time and speaker. Owners and deadlines appear only where the recording states them, "
        f"otherwise they are marked {UNSPEC}. The raw transcript is kept separately and unchanged.")


def _src(e: dict) -> dict:
    return {"quote": e["quote"], "speaker": e["speaker"], "time": e["time"], "start_seconds": e["start"], "segment_id": e["segment_id"]}


def build(job: str, s: dict) -> dict:
    v = views.report_view(job)
    nm = exports.names_map(job)
    ran = bool(v["final_check"])
    return {
        "schema_version": SCHEMA,
        "meeting": {**exports._meeting_block(job, s), "participants": [{"speaker": label_name(x["label"]), "name": nm.get(x["label"]), "role": x["role"], "share_of_speaking_percent": x["share"]} for x in sorted(v["speakers"], key=lambda y: y["label"])]},
        "status": {"final_check_ran": ran, "notes": v["attention"] + ([] if ran else [NOT_RUN])},
        "minutes": {
            "headline": v["headline"], "summary": [x["text"] for x in v["summary"]],
            "topics": [{"title": t["title"], "from": mmss(t["start"]), "to": mmss(t["end"]), "outcome": t["outcome"], "points": t["points"]} for t in v["topics"]],
            "discussion_points": [{"point": x["text"], "source": _src(x["evidence"])} for x in v["discussion_points"]],
            "proposals": [{"proposal": x["text"], "status": x["status"], "source": _src(x["evidence"])} for x in v["proposals"]],
            "open_questions": [{"question": x["text"], "source": _src(x["evidence"])} for x in v["open_questions"]],
            "risks": [{"risk": x["text"], "source": _src(x["evidence"])} for x in v["risks"]],
            "key_figures": [{"figure": x["label"], "value": x["value"], "source": _src(x["evidence"])} for x in v["key_figures"]],
        },
        "decisions": [{"id": d["id"], "decision": d["text"], "context": d["context"], "status": d["status"], "checked": d["checked"], "final_check": "not_run" if d["unconfirmed"] else "done", "source": _src(d["evidence"])} for d in v["decisions"]],
        "action_items": [{"id": a["id"], "task": a["task"], "owner": a["owner"] or UNSPEC, "owner_stated": bool(a["owner"]), "deadline": a["deadline"] or UNSPEC, "deadline_stated": bool(a["deadline"]),
                          "status": a["status"], "checked": a["checked"], "source": _src(a["evidence"])} for a in v["action_items"]],
        "corrections_in_force": exports.corrections_in_force(job),
        "raw_transcript": [{k: x[k] for k in ("id", "start", "end", "speaker", "speaker_name", "text")} for x in exports.raw_segments(job)],
        "refined_transcript": [{k: x[k] for k in ("id", "start", "end", "speaker", "speaker_name", "text", "changes")} for x in exports.refined_segments(job)],
    }


# ---------------------------------------------------------------------------------------------- CSV
def actions_csv(rec: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "task", "owner", "deadline", "status", "time", "speaker", "quote"])
    for a in rec["action_items"]:
        w.writerow([a["id"], a["task"], a["owner"], a["deadline"], a["status"], a["source"]["time"], a["source"]["speaker"], a["source"]["quote"]])
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- Markdown
def _cite(src: dict) -> str:
    return f'"{src["quote"]}" ({src["speaker"]}, {src["time"]})'


def _people(rec: dict) -> str:
    return ", ".join((p["name"] + f" ({p['speaker']})" if p["name"] else p["speaker"]) + (f", {p['role']}" if p["role"] else "") for p in rec["meeting"]["participants"]) or "Speakers were not distinguished in this recording"


def to_markdown(rec: dict, filename: str = "") -> str:
    m, mn = rec["meeting"], rec["minutes"]
    out = ["# Meeting record", "", f"- **File:** {filename or m['source_file'] or 'Recording'}", f"- **Date:** {m['date'] if m['date_provided'] else 'Not provided'}", f"- **Duration:** {mmss(m['duration_seconds'] or 0)}",
           f"- **Participants:** {_people(rec)}", ""]
    if not rec["status"]["final_check_ran"]:
        out += [f"> **{NOT_RUN}**", ""]
    if mn["headline"]:
        out += [f"**{mn['headline']}**", ""]
    if mn["summary"]:
        out += ["## Summary", ""] + [f"- {x}" for x in mn["summary"]] + [""]
    if mn["topics"]:
        out += ["## Discussion by topic", ""]
        for t in mn["topics"]:
            span = f"{t['from']} to {t['to']}{', ' + t['outcome'] if t['outcome'] else ''}"
            out += ([f"### {t['title']} ({span})", ""] + [f"- {p}" for p in t["points"]] if t["points"] else [f"- **{t['title']}** ({span})"]) + [""]
    out += ["## Key decisions", ""]
    if rec["decisions"]:
        for i, d in enumerate(rec["decisions"], 1):
            tag = "" if d["status"] == "confirmed" else f" [{d['status']}]"
            tag += " [not confirmed: final check did not run]" if d["final_check"] == "not_run" else ""
            out += [f"{i}. {d['decision']}{tag}", f"   - Source: {_cite(d['source'])}"] + ([f"   - Context: {d['context']}"] if d["context"] else [])
    else:
        out += ["No explicit decisions were identified in this recording."]
    out += ["", "## Action items", ""]
    if rec["action_items"]:
        for i, a in enumerate(rec["action_items"], 1):
            out += [f"{i}. {a['task']}", f"   - Owner: {a['owner']}", f"   - Deadline: {a['deadline']}", f"   - Source: {_cite(a['source'])}"]
    else:
        out += ["No actionable tasks were identified in this recording."]
    for title, key, field in (("Open questions", "open_questions", "question"), ("Risks and concerns", "risks", "risk")):
        if mn[key]:
            out += ["", f"## {title}", ""] + [f"- {x[field]} — {_cite(x['source'])}" for x in mn[key]]
    if mn["key_figures"]:
        out += ["", "## Key figures", ""] + [f"- {x['figure']}: **{x['value']}** — {_cite(x['source'])}" for x in mn["key_figures"]]
    if rec["status"]["notes"]:
        out += ["", "## Notes", ""] + [f"- {x}" for x in rec["status"]["notes"]]
    if rec["corrections_in_force"]:
        out += ["", "## Corrections applied to the transcript", ""] + [f'- {c["time"]}: "{c["original"]}" → "{c["corrected"]}"' for c in rec["corrections_in_force"]]
    return "\n".join(out + ["", f"_{FOOT}_", ""])


# ---------------------------------------------------------------------------------------------- HTML
CSS = """body{margin:0;background:#f3f0e8;color:#161616;font:16px/1.55 "IBM Plex Sans",system-ui,sans-serif}main{max-width:820px;margin:32px auto;background:#fff;padding:44px 52px;border:1px solid #ddd8cc}
h1{font-size:30px;margin:0 0 4px}h1:before{content:"";display:block;width:34px;height:4px;background:#f0a020;margin-bottom:16px}h2{font-size:15px;margin:34px 0 10px;padding-bottom:7px;border-bottom:1px solid #ddd8cc}
h3{font-size:15px;margin:18px 0 4px}dl{display:grid;grid-template-columns:130px 1fr;gap:4px 16px;margin:20px 0}dt{font:500 12px "IBM Plex Mono",monospace;text-transform:uppercase;letter-spacing:.06em;color:#6b6659}dd{margin:0}
.q{font:13px "IBM Plex Mono",monospace;color:#6b6659}.note{background:#fbe9c8;border-left:4px solid #f0a020;padding:10px 14px}.lead{font-size:19px;font-weight:600}.foot{font-size:13px;color:#6b6659;margin-top:34px}
.tag{font:12px "IBM Plex Mono",monospace;border:1px solid #999;padding:0 5px;margin-left:6px}@media print{body{background:#fff}main{border:0;margin:0;padding:0}}@media(max-width:700px){main{padding:24px 18px;margin:0}dl{grid-template-columns:1fr}}"""


def to_html(rec: dict, filename: str = "") -> str:
    e = html.escape
    m, mn = rec["meeting"], rec["minutes"]
    cite = lambda s: f'<div class="q">"{e(s["quote"])}" ({e(s["speaker"])}, {e(s["time"])})</div>'
    h = [f"<h1>Meeting record</h1><div class=\"q\">{e(filename or m['source_file'] or 'Recording')}</div>",
         f"<dl><dt>Date</dt><dd>{e(m['date']) if m['date_provided'] else 'Not provided'}</dd><dt>Duration</dt><dd>{e(mmss(m['duration_seconds'] or 0))}</dd><dt>Participants</dt><dd>{e(_people(rec))}</dd></dl>"]
    if not rec["status"]["final_check_ran"]:
        h.append(f'<p class="note"><b>{e(NOT_RUN)}</b></p>')
    if mn["headline"]:
        h.append(f'<p class="lead">{e(mn["headline"])}</p>')
    if mn["summary"]:
        h.append("<h2>Summary</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in mn["summary"]) + "</ul>")
    if mn["topics"]:
        h.append("<h2>Discussion by topic</h2>" + "".join(f'<h3>{e(t["title"])} <span class="q">{e(t["from"])}–{e(t["to"])}{" · " + e(t["outcome"]) if t["outcome"] else ""}</span></h3>' + ("<ul>" + "".join(f"<li>{e(p)}</li>" for p in t["points"]) + "</ul>" if t["points"] else "") for t in mn["topics"]))
    h.append("<h2>Key decisions</h2>")
    h.append("<ol>" + "".join(f'<li>{e(d["decision"])}{"" if d["status"] == "confirmed" else f" <span class=tag>{e(d["status"])}</span>"}{"<span class=tag>not confirmed</span>" if d["final_check"] == "not_run" else ""}{cite(d["source"])}</li>' for d in rec["decisions"]) + "</ol>"
             if rec["decisions"] else "<p>No explicit decisions were identified in this recording.</p>")
    h.append("<h2>Action items</h2>")
    h.append("<ol>" + "".join(f'<li>{e(a["task"])}<div>Owner: <b>{e(a["owner"])}</b> · Deadline: <b>{e(a["deadline"])}</b></div>{cite(a["source"])}</li>' for a in rec["action_items"]) + "</ol>"
             if rec["action_items"] else "<p>No actionable tasks were identified in this recording.</p>")
    for title, key, field in (("Open questions", "open_questions", "question"), ("Risks and concerns", "risks", "risk")):
        if mn[key]:
            h.append(f"<h2>{title}</h2><ul>" + "".join(f"<li>{e(x[field])}{cite(x['source'])}</li>" for x in mn[key]) + "</ul>")
    if mn["key_figures"]:
        h.append("<h2>Key figures</h2><ul>" + "".join(f"<li>{e(x['figure'])}: <b>{e(x['value'])}</b>{cite(x['source'])}</li>" for x in mn["key_figures"]) + "</ul>")
    if rec["status"]["notes"]:
        h.append("<h2>Notes</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in rec["status"]["notes"]) + "</ul>")
    if rec["corrections_in_force"]:
        h.append("<h2>Corrections applied to the transcript</h2><ul>" + "".join(f'<li>{e(c["time"])}: "{e(c["original"])}" → "{e(c["corrected"])}"</li>' for c in rec["corrections_in_force"]) + "</ul>")
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Meeting record</title><style>{CSS}</style></head><body><main>{"".join(h)}<p class="foot">{e(FOOT)}</p></main></body></html>'
