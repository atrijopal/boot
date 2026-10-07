"""The final report as one self-contained HTML page (no scripts, no external files). Built only from the checked Result, never by a model."""
import json
from html import escape as e
from pathlib import Path

from app.config.settings import settings

from app.core.lenses import LENS_ORDER, LENSES
from app.core import insights
from app.core.wording import plain
from app.core.report import BADGE, label_name, mmss, names, owner_of, registers, who
from app.core.schemas import Record, Result

CSS = """
:root{--bg:#f4f6fb;--card:#fff;--ink:#1b2130;--muted:#667085;--line:#e4e8f0;--brand:#3b5bdb;--brand2:#7048e8;--ok:#0b7a43;--okbg:#e3f6ec;--warn:#9a5b00;--warnbg:#fff1d6;--bad:#b42318;--badbg:#fde8e6;--code:#f1f3f9}
@media (prefers-color-scheme:dark){:root{--bg:#0e1220;--card:#161b2e;--ink:#e8ecf6;--muted:#9aa5bd;--line:#262d45;--brand:#8ea2ff;--brand2:#b197fc;--ok:#5ad08a;--okbg:#10301f;--warn:#f0b45a;--warnbg:#38290f;--bad:#ff8a80;--badbg:#3a1815;--code:#1d233a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:0 18px 48px}
.hero{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;padding:34px 0 64px}
.hero .wrap{padding-bottom:0}.hero h1{margin:0 0 4px;font-size:30px;letter-spacing:-.5px}.hero p{margin:0;opacity:.9}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.meta span{background:rgba(255,255,255,.18);border-radius:999px;padding:2px 12px;font-size:13px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:-40px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:0 2px 8px rgba(20,30,70,.06)}
.stat b{display:block;font-size:26px;line-height:1.1}.stat span{color:var(--muted);font-size:12.5px}
section{margin-top:30px}h2{font-size:18px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
h2 .n{background:var(--code);color:var(--muted);font-size:12px;font-weight:600;border-radius:999px;padding:0 9px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.headline{font-size:17px;font-weight:600;margin:0 0 8px}.summary li{margin:7px 0}.refs{color:var(--muted);font-size:12px}.refs a{color:var(--muted)}
ul{margin:4px 0;padding-left:20px}.muted{color:var(--muted)}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 9px;font-size:12px;line-height:20px;color:var(--muted);white-space:nowrap}
.chip.ok{border-color:var(--ok);color:var(--ok);background:var(--okbg)}.chip.warn{border-color:var(--warn);color:var(--warn);background:var(--warnbg)}.chip.bad{border-color:var(--bad);color:var(--bad);background:var(--badbg)}
.rec{border-left:4px solid var(--brand);}.rec.k-decision{border-left-color:#2f9e44}.rec.k-action{border-left-color:#f08c00}.rec.k-question{border-left-color:#1c7ed6}.rec.k-risk{border-left-color:#e03131}.rec.k-fact{border-left-color:#7048e8}.rec.k-proposal{border-left-color:#0ca678}.rec.k-disagreement{border-left-color:#d6336c}
.rec .top{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}.rec .txt{font-weight:600}.id{font:12px ui-monospace,Menlo,monospace;color:var(--muted)}
.quote{margin:6px 0 2px;padding-left:10px;border-left:3px solid var(--line);color:var(--muted);font-style:italic}.quote small{font-style:normal}
.kv{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px;font-size:14px}.kv b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;color:var(--muted);font-size:12px;font-weight:600}td,th{padding:7px 10px 7px 0;border-bottom:1px solid var(--line);vertical-align:top}
.tl{position:relative;margin-left:8px;padding-left:18px;border-left:2px solid var(--line)}.tl .t{position:relative;margin:0 0 14px}.tl .t:before{content:"";position:absolute;left:-25px;top:6px;width:10px;height:10px;border-radius:50%;background:var(--brand)}
.tl .when{color:var(--muted);font-size:12.5px}.tl h3{margin:0;font-size:15px}
.people{display:flex;flex-wrap:wrap;gap:8px}.person{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 14px}
del{background:var(--badbg);text-decoration:line-through}ins{background:var(--okbg);text-decoration:none}
details{margin:8px 0}summary{cursor:pointer;color:var(--brand);font-weight:600}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.step b{display:block}.step .muted{font-size:12.5px}
.note{background:var(--warnbg);border:1px solid var(--warn);color:var(--warn);border-radius:10px;padding:8px 12px;margin:10px 0}
footer{margin-top:36px;color:var(--muted);font-size:12.5px;text-align:center}
a.ln{font-size:11.5px;font-style:normal;margin-left:6px;color:var(--brand)}
.spk{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.spk .card{margin:0}.spk h3{margin:0 0 2px;font-size:15px}
.bar{height:6px;border-radius:3px;background:var(--code);overflow:hidden;margin:6px 0}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--brand),var(--brand2))}
.spk ul{padding-left:18px;margin:2px 0;font-size:13.5px}.role{font-size:13px;color:var(--brand);font-weight:600}.spk .lbl{font-size:12px;color:var(--muted);margin-top:6px}
.own h3{margin:8px 0 2px;font-size:14px}.own li{margin:2px 0}
.tlist .t{display:flex;gap:10px;margin:6px 0;align-items:baseline}.tlist .when{color:var(--muted);font-size:12.5px;min-width:42px}
tr.cited td{background:var(--code)}tr:target td{background:var(--warnbg);outline:1px solid var(--warn)}
.tr .rid{font-size:11px}
@media print{.hero{background:none;color:#000;padding:0}.hero .meta span{background:none;border:1px solid #999}.stat,.card{box-shadow:none;break-inside:avoid}details{display:block}}
@media (max-width:560px){.hero h1{font-size:24px}}
"""
STATUS_CLS = {"confirmed": "ok", "accepted": "ok", "completed": "ok", "answered": "ok", "resolved": "ok", "committed": "ok",
              "revoked": "bad", "rejected": "bad", "cancelled": "bad", "withdrawn": "bad", "unresolved": "warn", "uncertain": "warn", "open": "warn", "raised": "warn"}
OUTCOME_CLS = {"decided": "ok", "open": "warn", "info": ""}
CHECK_CLS = {"code": "", "code+model": "ok", "needs_review": "warn"}
STAGES = [("transcribe", "1 Transcript", "speech-to-text API"), ("speakers", "2a Speaker labels", "language model, inferred from the text"), ("refine", "2b Refinement", "language model + code gate"),
          ("extract", "3 Extraction", "language model + code gate"), ("review", "4 Review and summary", "language model + code check")]


def _filename(r: Result) -> str:
    try:
        return json.loads((Path(settings.outputs_dir) / r.job_id / "upload.json").read_text())["filename"] or r.audio.original_file
    except (OSError, ValueError, KeyError, TypeError):
        return r.audio.original_file


def _refs(ids: list[str]) -> str:
    return " ".join(f'<a href="#{e(i)}">{e(i)}</a>' for i in ids)


def _ln(rec: Record) -> str:
    ids = rec.evidence[0].segment_ids
    return f'<a class="ln" href="#t-{e(ids[0])}">&#8599; transcript</a>' if ids else ""


def _quote(rec: Record, nm: dict[str, str]) -> str:
    q = rec.evidence[0]
    return f'<div class="quote">&ldquo;{e(" ".join(q.quote.split()))}&rdquo; <small>&mdash; {e(who(q.speaker, nm))}, {mmss(q.start)}</small>{_ln(rec)}</div>'


def _rec(rec: Record, nm: dict[str, str], ctx: dict | None = None) -> str:
    extra = ""
    if rec.kind == "action":
        o, dl = owner_of(rec, nm), rec.attrs.get("deadline")
        extra = f'<div class="kv"><span>Owner: <b>{e(o)}</b></span><span>Deadline: <b>{e(dl) if dl else "unspecified"}</b></span></div>'
    if rec.kind == "fact":
        extra = f'<div class="kv"><span>{e(str(rec.attrs.get("label", "")))}: <b>{e(str(rec.attrs.get("value", "")))}</b></span></div>'
    notes = "".join(f'<div class="muted">{e(n)}</div>' for n in rec.notes)
    if ctx and rec.rid in ctx:
        extra += f'<div class="kv"><span><b>Context:</b> {e(ctx[rec.rid].text)}</span></div>'
    return (f'<div class="card rec k-{rec.kind}" id="{e(rec.rid)}"><div class="top"><span class="id">{e(rec.rid)}</span>'
            f'<span class="chip {STATUS_CLS.get(rec.status, "")}">{e(rec.status)}</span><span class="txt">{e(" ".join(rec.text.split()))}</span></div>'
            f'{extra}{_quote(rec, nm)}<div><span class="chip {CHECK_CLS[rec.check]}">{e(BADGE[rec.check])}</span></div>{notes}</div>')


def _steps(r: Result) -> str:
    cells = [("1 Transcript", "speech converted to text"), ("2 Refinement", "recognition errors in terms checked, every change shown"), ("3 Extraction", "decisions, tasks, questions and figures read from the transcript"),
             ("4 Review", "items checked against the recording and summarised")]
    return '<div class="steps">' + "".join(f'<div class="card step"><b>{e(t)}</b><span class="muted">{e(d)}</span></div>' for t, d in cells) + "</div>"


def _transcript(r: Result) -> str:
    by_seg: dict[str, list] = {}
    for ed in r.edits:
        if ed.status == "applied" and ed.char_start is not None:
            by_seg.setdefault(ed.segment_id, []).append(ed)
    cited: dict[str, list[str]] = {}
    for rec in r.records:
        for ev in rec.evidence:
            for sid in ev.segment_ids:
                cited.setdefault(sid, []).append(rec.rid)
    rows = []
    for s, rs in zip(r.transcript.segments, r.refined.segments):
        out, pos = "", 0
        for ed in sorted(by_seg.get(s.segment_id, []), key=lambda x: x.char_start):
            out += e(s.text[pos : ed.char_start]) + f"<ins>{e(ed.replace)}</ins>"
            pos = ed.char_end
        out += e(s.text[pos:])
        sp = rs.speaker_name or (label_name(s.speaker) if s.speaker_known else "")
        refs = "".join(f' <a class="rid" href="#{e(i)}">{e(i)}</a>' for i in dict.fromkeys(cited.get(s.segment_id, [])))
        rows.append(f'<tr id="t-{e(s.segment_id)}" class="{"cited" if s.segment_id in cited else ""}"><td class="muted">{mmss(s.start)}</td><td>{e(sp)}</td><td>{out}{refs}</td></tr>')
    return "<table>" + "".join(rows) + "</table>"


def _speakers(r: Result) -> str:
    sp = insights.speaker_summary(r)
    if not sp:
        return ""
    cards = []
    for a in sp:
        def lst(label: str, recs: list) -> str:
            if not recs:
                return ""
            return f'<div class="lbl">{label}</div><ul>' + "".join(f'<li><a href="#{e(x.rid)}">{e(" ".join(x.text.split()))}</a></li>' for x in recs[:4]) + "</ul>"

        cards.append(f'<div class="card"><h3>{e(a["name"])}</h3>{f'<div class="role">{e(a["role"])}</div>' if a["role"] else ""}<span class="muted">{e(label_name(a["speaker"]))} &middot; {a["lines"]} lines &middot; {a["words"]} words &middot; {mmss(a["seconds"])} speaking</span>'
                     f'<div class="bar"><i style="width:{a["share"]}%"></i></div><span class="muted">{a["share"]}% of the talk time</span>'
                     + ("<ul>" + "".join(f'<li>{e(c.text)} <span class="refs">{_refs(c.record_ids)}</span></li>' for c in a["summary"]) + "</ul>" if a["summary"] else "")
                     + lst("Committed to", a["commitments"]) + lst("Asked", a["questions"]) + lst("Proposed", a["proposals"]) + lst("Raised concerns", a["risks"]) + "</div>")
    note = '<p class="muted">Speaker labels were worked out from the text by a model, not from voices, so they can be wrong.</p>' if r.speakers_inferred else ""
    return f'<section><h2>Speakers<span class="n">{len(sp)}</span></h2><div class="spk">{"".join(cards)}</div>{note}</section>'


def _owners(r: Result) -> str:
    groups = insights.tasks_by_owner(r)
    if not groups:
        return ""
    body = "".join(f'<h3>{e(o if o != "unspecified" else "No owner stated")}</h3><ul>' + "".join(
        f'<li><a href="#{e(x.rid)}">{e(" ".join(x.text.split()))}</a>' + (f' <span class="muted">(by {e(x.attrs["deadline"])})</span>' if x.attrs.get("deadline") else "") + "</li>" for x in recs) + "</ul>" for o, recs in groups)
    return f'<div class="card own"><b>Tasks by owner</b>{body}</div>'


def _timeline(r: Result) -> str:
    items = insights.timeline(r)
    if not items:
        return ""
    rows = "".join(f'<div class="t"><span class="when">{mmss(x.start)}</span><span class="chip {STATUS_CLS.get(x.status, "")}">{e(x.kind)}: {e(x.status)}</span><span><a href="#{e(x.rid)}">{e(" ".join(x.text.split()))}</a></span></div>' for x in items)
    return f'<section><h2>Decision timeline<span class="n">{len(items)}</span></h2><div class="card tlist">{rows}</div></section>'


def to_html(r: Result) -> str:
    nm = names(r)
    reg = registers(r)
    u = r.usage
    counts = [("Decisions", len(reg["decisions"])), ("Action items", len(reg["action_items"])), ("Open questions", sum(1 for x in reg["open_questions"] if x.status == "open")),
              ("Risks", len(reg["risks"])), ("Key figures", len(reg["key_figures"])), ("Topics", len(r.topics))]
    h = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Meeting report</title><style>{CSS}</style></head><body>',
         f'<div class="hero"><div class="wrap"><h1>Meeting report</h1><p>{e(_filename(r))}</p><div class="meta"><span>{mmss(r.audio.duration_seconds)} recording</span>'
         f'<span>{len(r.speaker_names) or ("no")} named speaker(s)</span><span></span><span>status: {e(r.status)}</span></div></div></div>',
         '<div class="wrap"><div class="stats">' + "".join(f'<div class="stat"><b>{n}</b><span>{e(t)}</span></div>' for t, n in counts) + "</div>"]
    if r.status == "partial":
        h.append('<div class="note"><b>Incomplete.</b> Some steps did not finish (see Notes at the end). Parts of this report may be missing.</div>')
    head = f'<p class="headline">{e(r.headline.text)} <span class="refs">{_refs(r.headline.record_ids)}</span></p>' if r.headline else ""
    att = insights.attention(r)
    if att:
        h.append('<section><h2>Needs attention</h2><div class="card"><ul>' + "".join(f"<li>{e(x)}</li>" for x in att) + "</ul></div></section>")
    h.append('<section class="summary"><h2>Summary</h2><div class="card">' + head + ("<ul>" + "".join(f'<li>{e(c.text)} <span class="refs">{_refs(c.record_ids)}</span></li>' for c in r.summary) + "</ul>" if r.summary else '<span class="muted">No verified outcomes were extracted.</span>') + "</div></section>")
    h.append(_speakers(r))
    if r.topics:
        h.append('<section><h2>Topics<span class="n">' + str(len(r.topics)) + '</span></h2><div class="card"><div class="tl">' + "".join(
            f'<div class="t"><div class="when">{mmss(t.start)} &ndash; {mmss(t.end)}</div><h3>{e(t.title)} {f'<span class="chip {OUTCOME_CLS[t.outcome]}">{t.outcome}</span>' if t.outcome else ""}</h3>' +
            ("<ul>" + "".join(f'<li>{e(b.text)} <span class="refs">{_refs(b.record_ids)}</span></li>' for b in t.bullets) + "</ul>" if t.bullets else "") + "</div>" for t in r.topics) + "</div></div></section>")
    h.append(_timeline(r))
    for k in LENS_ORDER:
        recs = reg[{"decision": "decisions", "action": "action_items", "question": "open_questions", "proposal": "proposals", "disagreement": "disagreements", "risk": "risks", "fact": "key_figures", "point": "discussion_points"}[k]]
        h.append(f'<section><h2>{e(LENSES[k]["title"])}<span class="n">{len(recs)}</span></h2>')
        if k == "action" and recs:
            h.append('<div class="card"><table><tr><th>Task</th><th>Owner</th><th>Deadline</th><th>Status</th></tr>' + "".join(
                f'<tr id="{e(x.rid)}"><td>{e(" ".join(x.text.split()))}<div class="quote">&ldquo;{e(" ".join(x.evidence[0].quote.split()))}&rdquo; <small>&mdash; {e(who(x.evidence[0].speaker, nm))}, {mmss(x.start)}</small>{_ln(x)}</div>'
                f'<span class="chip {CHECK_CLS[x.check]}">{e(BADGE[x.check])}</span></td><td><b>{e(owner_of(x, nm))}</b></td><td>{e(x.attrs.get("deadline") or "unspecified")}</td><td><span class="chip {STATUS_CLS.get(x.status, "")}">{e(x.status)}</span></td></tr>' for x in recs) + "</table></div>" + _owners(r))
        elif recs and k == "point":  # many short points: a compact list, each linked to its place in the transcript
            h.append('<div class="card"><ul>' + "".join(f'<li id="{e(x.rid)}">{e(" ".join(x.text.split()))} <span class="muted">&mdash; {e(who(x.evidence[0].speaker, nm))}, {mmss(x.start)}</span>{_ln(x)}</li>' for x in recs) + "</ul></div>")
        elif recs:
            h.append("".join(_rec(x, nm, r.decision_context) for x in recs))
        else:
            h.append('<div class="card muted">None recorded in this meeting.</div>')
        h.append("</section>")
    if r.edits:
        h.append(f'<section><h2>Transcript corrections<span class="n">{len(r.edits)}</span></h2><div class="card"><table><tr><th>Time</th><th>Change</th><th>Status</th><th>Why</th></tr>' + "".join(
            f'<tr><td class="muted">{mmss(x.start)}</td><td><del>{e(x.find)}</del> &rarr; <ins>{e(x.replace)}</ins></td><td><span class="chip {"ok" if x.status == "applied" else "bad" if x.status == "rejected" else "warn"}">{e(x.status)}</span></td>'
            f'<td class="muted">{e(x.reject_reason or "; ".join(s.detail for s in x.support) or x.kind)}</td></tr>' for x in r.edits) + "</table></div></section>")
    h.append('<section><h2>How this report was made</h2>' + _steps(r) +
             '<p class="muted">Every quote, number, owner, deadline and transcript fix was checked by code against the transcript. Models only propose; the report is assembled by code.</p></section>')
    if r.warnings:
        h.append('<section><h2>Notes and limitations</h2><div class="card"><ul>' + "".join(f"<li>{e(w)}</li>" for w in r.warnings) + "</ul></div></section>")
    h.append(f'<section id="transcript"><h2>Transcript<span class="n">{len(r.transcript.segments)}</span></h2><p class="muted">Lines that back a record are shaded and link to it; transcript fixes are highlighted.</p><div class="card tr">{_transcript(r)}</div></section>')
    h.append(f"<footer>Meeting record &middot; {e(r.job_id)}</footer></div></body></html>")
    return "".join(h)
