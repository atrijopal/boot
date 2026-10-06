"""Human-readable (Markdown) and machine-readable (JSON, CSV) outputs, built from the same Minutes object."""
import csv
import io

from app.minutes.chunking import mmss
from app.minutes.lenses import LENSES, LENS_ORDER
from app.schemas.minutes import Claim, Item, Minutes

PLURAL = {"decision": "decisions", "action": "action_items", "question": "open_questions", "proposal": "proposals",
          "disagreement": "disagreements", "risk": "risks", "fact": "key_figures"}
SCHEMA_VERSION = "minutes/1"


def registers(m: Minutes) -> dict[str, list[Item]]:
    return {PLURAL[l]: [i for i in m.items if i.lens == l] for l in LENS_ORDER}


def to_export(m: Minutes) -> dict:
    """Machine-readable record. `registers` holds the very same item objects the Markdown is written from."""
    d = m.model_dump()
    d["schema_version"] = SCHEMA_VERSION
    d["registers"] = {k: [i.model_dump() for i in v] for k, v in registers(m).items()}
    return d


def owner_of(it: Item) -> str:
    return it.attrs["owner"]["text"] or "unspecified"


def _clean(s: str) -> str:
    return " ".join(str(s).split())


def _refs(c: Claim, flagged: set[str] | None = None) -> str:
    ids = list(dict.fromkeys(c.item_ids))
    note = " (a cited figure is flagged for review; see Key figures)" if flagged and any(i in flagged for i in ids) else ""
    return (f" _[{', '.join(ids)}]_" if ids else "") + note


def _evidence(it: Item) -> str:
    e = it.evidence[0]
    who = e.speaker_name or (e.speaker if e.speaker != "SPEAKER_UNKNOWN" else "unknown speaker")
    return f'"{_clean(e.quote)}" ({who}, {mmss(e.start)}, {e.segment_id})'


def _item_md(it: Item) -> str:
    head = f"- **{it.item_id}** [{it.status}] {_clean(it.text)}"
    if it.lens == "action":
        head += f"\n  - Owner: {owner_of(it)}" + (f" ({it.attrs['owner']['basis'].replace('_', ' ')})" if it.attrs['owner']['basis'] != "unspecified" else "")
        head += f" | Deadline: {it.attrs.get('deadline') or 'unspecified'}"
    if it.lens == "fact":
        head = f"- **{it.item_id}** {_clean(it.attrs['label'])}: **{_clean(it.attrs['value'])}** ({_clean(it.text)})"
        for f in it.attrs.get("flags", []):
            head += f"\n  - WARNING {f}"
    if it.superseded_by:
        head += f"\n  - Replaced by {it.superseded_by}"
    if it.resolved_by:
        head += f"\n  - Answered by {it.resolved_by}"
    return head + f"\n  - {_evidence(it)}"


def to_markdown(m: Minutes) -> str:
    out = ["# Meeting minutes", ""]
    mm = m.meta
    out.append(f"Recording length {mmss(mm.duration_seconds)} | {mm.segments} transcript segments | run `{mm.run_id}` | status **{m.status}**")
    out.append("")
    out.append("_Every item below is backed by a quote from the transcript. Items were checked by a second model; checking reduces errors but is not proof of truth._")
    if m.status == "partial":
        out += ["", "> **INCOMPLETE:** some steps did not finish or fell back (see Notes at the end). Parts of this record may be missing."]
    flagged = {i.item_id for i in m.items if i.lens == "fact" and i.attrs.get("flags")}
    out += ["", "## Executive summary", ""]
    out += [f"- {_clean(c.text)}{_refs(c, flagged)}" for c in m.executive_summary] or ["No verified outcomes were extracted for this meeting."]
    out += ["", "## Participants", ""]
    out += [f"- {p.name + ' (' + p.speaker + ')' if p.name else p.speaker}: {mmss(p.talk_seconds)} of speech, {p.segments} segments" for p in m.participants] or ["Speaker labels were not available."]
    out += ["", "## Topics", ""]
    for t in m.topics:
        out += [f"### {t.title} ({mmss(t.start)} to {mmss(t.end)})", ""]
        out += [f"- {_clean(c.text)}{_refs(c, flagged)}" for c in t.claims] or ["- No verified narrative for this topic."]
        if t.note:
            out.append(f"\n_{t.note}_")
        out.append("")
    reg = registers(m)
    for lens in LENS_ORDER:
        out += [f"## {LENSES[lens]['title']}", ""]
        items = reg[PLURAL[lens]]
        out += [_item_md(i) for i in items] if items else [f"None recorded in this meeting."]
        out.append("")
    if m.needs_review:
        out += ["## Items needing review", "", "_Extracted and quote-checked, but the verifier did not confirm them. Not used in the summaries._", ""]
        for i in m.needs_review:
            out.append(f"- **{i.item_id}** ({LENSES[i.lens]['title']}, {i.status}) {_clean(i.text)}\n  - {_evidence(i)}\n  - Why: {'; '.join(i.verification.notes) or 'unverified'}")
        out.append("")
    if m.relations:
        out += ["## Relations between items", ""]
        out += [f"- {r.source} **{r.kind}** {r.target}: \"{_clean(r.quote)}\" ({r.segment_id})" for r in m.relations]
        out.append("")
    out += ["## Notes and limitations", ""]
    out += [f"- {_clean(w)}" for w in m.warnings] or ["- None."]
    return "\n".join(out) + "\n"


def actions_csv(m: Minutes) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "task", "status", "owner", "owner_basis", "deadline", "time", "quoted_by", "quote", "segment_id"])
    for i in registers(m)["action_items"]:
        e = i.evidence[0]
        w.writerow([i.item_id, i.text, i.status, owner_of(i), i.attrs["owner"]["basis"], i.attrs.get("deadline") or "unspecified", mmss(i.start),
                    e.speaker_name or e.speaker, e.quote, e.segment_id])
    return buf.getvalue()
