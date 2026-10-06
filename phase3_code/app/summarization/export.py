"""Canonical registers and safe Markdown exports; no second extraction pass."""
import html
import re

from app.summarization.events import atomic_json
from app.summarization.summaries import registers

ARTIFACTS = {
    "minutes": ("minutes.md", "text/markdown"),
    "json": ("minutes.json", "application/json"),
    "graph": ("knowledge_graph.json", "application/json"),
    "topics": ("topics.json", "application/json"),
    "actions": ("action_items.json", "application/json"),
    "decisions": ("decisions.json", "application/json"),
    "questions": ("open_questions.json", "application/json"),
    "validation": ("validation_report.json", "application/json"),
    "manifest": ("manifest.json", "application/json"),
    "events": ("events.json", "application/json"),
    "units": ("units.json", "application/json"),
}


def md(text):
    value = html.escape(str(text)).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", value)


def citations(refs):
    return ", ".join(f"[{r.unit_id}](#{r.unit_id})" for r in {r.unit_id: r for r in refs}.values())


def render_minutes(result):
    manifest = result.manifest
    rows = ["# Meeting minutes", "", f"Run status: **{manifest.status}**. Source revision: `{manifest.source_revision[:12]}`.", "",
            "These minutes summarize recorded statements; model verification does not establish external truth.", ""]
    if manifest.status != "complete":
        rows += ["> INCOMPLETE: some processing or reconciliation did not finish. Review the manifest and rerun to resume.", ""]
    if manifest.warnings:
        rows += ["## Review notes", ""] + ["- " + md(w) for w in manifest.warnings] + [""]
    rows += ["## Executive summary", ""]
    rows += [f"- {md(c.text)} ({citations(c.evidence)})" for c in result.executive_summary] or ["No supported outcomes were extracted."]
    rows += ["", "## Topic minutes", ""]
    summaries = {s.topic_id: s for s in result.topic_summaries}
    for t in result.topics:
        share = f"{t.duration_share:.1%} of known-duration speaker-time" if t.duration_share is not None else "Duration share unavailable"
        rows += [f"### {md(t.title)}", "", f"{share}; {len(t.occurrences)} occurrence(s).", ""]
        summary = summaries.get(t.topic_id)
        rows += [f"- {md(c.text)} ({citations(c.evidence)})" for c in summary.claims] if summary and summary.claims else ["No verified narrative available; inspect topic evidence."]
        rows += [""]
    regs = registers(result.events)
    rows += ["## Decisions (including revision history)", ""]
    rows += [f"- **{md(e.status)}**: {md(e.description)} ({citations(e.evidence)}; `{e.event_id}`)" for e in regs["decisions"]] or ["None extracted."]
    rows += ["", "## Action items", "", "| Task | Status | Owner as stated | Deadline as stated | Evidence |", "| --- | --- | --- | --- | --- |"]
    for e in regs["action_items"]:
        owner = e.owner_text or e.owner_speaker_id or "Not stated"
        rows.append(f"| {md(e.description)} | {md(e.status)} | {md(owner)} | {md(e.deadline_text or 'Not stated')} | {citations(e.evidence)} |")
    if not regs["action_items"]:
        rows.append("| None extracted | — | — | — | — |")
    for key, title in (("open_questions", "Open questions"), ("risks", "Stated risks")):
        rows += ["", "## " + title, ""]
        rows += [f"- {md(e.description)} ({citations(e.evidence)})" for e in regs[key]] or ["None extracted."]
    rows += ["", "## Revisions and unresolved conflicts", ""]
    rows += [f"- `{r.source}` **{r.kind}** `{r.target}` ({citations(r.evidence)})" for r in result.relations if r.kind in ("SUPERSEDES", "CONTRADICTS")] or ["No supported revision/conflict links extracted."]
    review = [e for e in result.events if e.validation_status != "supported"]
    if review:
        rows += ["", "## Unverified extraction proposals — review required", ""]
        rows += [f"- {md(e.description)} — {md('; '.join(e.validation_notes))} ({citations(e.evidence)})" for e in review]
    rows += ["", "## Source evidence", "", "Times may be proportional estimates within a source segment.", ""]
    for u in result.units:
        rows += [f"### {u.unit_id}", "", f"{u.start_seconds:.2f}–{u.end_seconds:.2f}s · {md(u.speaker_name or u.speaker_id or 'Unknown/mixed speaker')} · segment `{md(u.segment_id)}` · characters {u.start_char}:{u.end_char}", "", "> " + md(u.text), ""]
    return "\n".join(rows)


def save_artifacts(folder, result):
    regs = {k: [e.model_dump() for e in v] for k, v in registers(result.events).items()}
    data = {
        "manifest": result.manifest.model_dump(), "units": [u.model_dump() for u in result.units],
        "topics": [t.model_dump() for t in result.topics],
        "topic_assignments": [{"unit_id": u.unit_id, "memberships": u.memberships, "unresolved_weight": u.unresolved_weight, "membership_calibrated": False} for u in result.units],
        "events": [e.model_dump() for e in result.events], "relations": [r.model_dump() for r in result.relations],
        "knowledge_graph": result.knowledge_graph.model_dump(),
        "topic_summaries": [t.model_dump() for t in result.topic_summaries],
        "validation_report": result.validation_report, **regs,
        "minutes": {"manifest": result.manifest.model_dump(), "executive_summary": [c.model_dump() for c in result.executive_summary],
                    "topic_summaries": [t.model_dump() for t in result.topic_summaries], **regs},
        "phase3_result": result.model_dump(),
    }
    for name, value in data.items():
        atomic_json(folder / f"{name}.json", value)
    (folder / "minutes.md").write_text(render_minutes(result), encoding="utf-8")
