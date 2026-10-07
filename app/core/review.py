"""Step 4: ONE model call that reviews the extracted records, writes the executive summary and a few bullets per topic.

The model sees only the records (plus the transcript lines around decisions and actions, for the faithfulness check), never the whole transcript.
Its verdicts can only remove or weaken things (doubt a record, fix a status, clear an owner or deadline); every summary sentence is checked by code.
"""
import json
import re

from app.config.settings import settings
from app.core.gates import numbers_in
from app.core.lenses import LENSES, STATUS_MEANING
from app.core.llm import Meter, ask
from app.core.schemas import Claim, Record, Topic
from app.core.units import Unit, line

CITATION = re.compile(r"\s*[\(\[]\s*\d+(?:\s*[,;]\s*\d+)*\s*[\)\]]")  # "(0, 2)": record numbers the model wrote into the sentence; they belong in "i"
PLURAL = {"decisions": "decision", "actions": "action", "proposals": "proposal", "questions": "question", "risks": "risk", "facts": "fact", "disagreements": "disagreement"}
MAX_LISTED = 100  # records shown to the model
PRIORITY = {"decision": 0, "action": 1, "proposal": 2, "risk": 3, "question": 4, "fact": 5, "disagreement": 6, "point": 7}  # kept first when there are more records than MAX_LISTED
MAX_CHECKED = 30  # records that also get transcript context and a verdict


def kinds_in_scope(scope: str) -> set[str]:
    s = (scope or "").strip().lower()
    if s in ("", "none"):
        return set()
    if s == "all":
        return set(LENSES)
    return {PLURAL.get(p.strip(), p.strip()) for p in s.split(",") if PLURAL.get(p.strip(), p.strip()) in LENSES}


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def build_input(records: list[Record], topics: list[Topic], units: list[Unit]) -> tuple[str, list[Record], set[int]]:
    """Returns (model input, the records listed in order, numbers of the records that carry context for a verdict)."""
    order = {u.uid: i for i, u in enumerate(units)}
    scope = kinds_in_scope(settings.review_scope)
    keep = sorted(range(len(records)), key=lambda i: (PRIORITY.get(records[i].kind, 9), i))[:MAX_LISTED]
    listed = [records[i] for i in sorted(keep)]  # still in time order
    checked: set[int] = set()
    lines = []
    for n, r in enumerate(listed):
        who_ = r.evidence[0].speaker
        rec: dict = {"n": n, **({"w": who_} if who_ != "SPEAKER_UNKNOWN" else {}), "k": r.kind, "s": r.status, "x": r.text, "q": " ".join(r.evidence[0].quote.split()[:25])}
        if r.kind == "fact":
            rec["v"] = r.attrs.get("value")
        if r.kind == "action":
            if r.attrs["owner"]["text"]:
                rec["o"] = r.attrs["owner"]["text"]
            if r.attrs.get("deadline"):
                rec["d"] = r.attrs["deadline"]
        if r.kind in scope and len(checked) < MAX_CHECKED:
            ctx: list[str] = []
            for e in r.evidence:
                k = order[e.uid]
                for u in units[max(0, k - 1) : k + 2]:
                    if line(u) not in ctx:
                        ctx.append(line(u))
            rec["m"] = STATUS_MEANING.get((r.kind, r.status), "")
            rec["c"] = ctx
            checked.add(n)
        lines.append(json.dumps(rec, ensure_ascii=False))
    tops = "\n".join(f"{i} | {t.title} | {_mmss(t.start)}-{_mmss(t.end)}" for i, t in enumerate(topics)) or "(none)"
    return f"TOPICS:\n{tops}\n\nRECORDS:\n" + "\n".join(lines), listed, checked


def apply_verdicts(listed: list[Record], checked: set[int], verdicts: list) -> None:
    """Marks checked records `code+model` or `needs_review`; may fix a status or clear an owner / deadline the context does not support."""
    by_n = {v.get("n"): v for v in verdicts if isinstance(v, dict)}
    for n in sorted(checked):
        r, v = listed[n], by_n.get(n)
        if v is None:
            r.notes.append("no model verdict; checked by code only")
            continue
        better = v.get("st")
        if v.get("ok") is not True and better in LENSES[r.kind]["statuses"] and better != r.status:
            r.notes.append(f"status corrected from '{r.status}' to '{better}' by the review")
            r.status, v = better, {**v, "ok": True}
        if v.get("ok") is True:
            r.check = "code+model"
            if r.kind == "action":
                if r.attrs["owner"]["basis"] != "unspecified" and v.get("own") is False:
                    r.notes.append(f"owner removed: not supported by the context ({r.attrs['owner']['text']})")
                    r.attrs["owner"] = {"text": None, "basis": "unspecified", "speaker": None}
                if r.attrs.get("deadline") and v.get("dl") is False:
                    r.notes.append(f"deadline removed: not supported by the context ({r.attrs['deadline']})")
                    r.attrs["deadline"] = None
        else:
            r.check = "needs_review"
            r.notes.append("the review doubted this record (statement or status not supported)")


def claims(raw, listed: list[Record]) -> list[Claim]:
    """Summary sentences / topic bullets that pass: they cite real, not-doubted records and add no number the records do not contain."""
    out: list[Claim] = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        text = " ".join(CITATION.sub("", str(s.get("x") or "")).split())
        cited = s.get("i") if isinstance(s.get("i"), list) else []
        recs = [listed[i] for i in cited if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(listed) and listed[i].check != "needs_review"]
        source = " ".join(r.text + " " + str(r.attrs.get("value", "")) + " " + " ".join(e.quote for e in r.evidence) for r in recs)
        if not 3 <= len(text) <= 600 or not recs or not numbers_in(text) <= numbers_in(source):
            continue
        out.append(Claim(text=text, record_ids=list(dict.fromkeys(r.rid for r in recs))))
    return out


def extractive(records: list[Record]) -> list[Claim]:
    """Fallback when the model's summary is missing or fails the checks: the verified records' own quotes, never new wording. Tasks are not repeated here (they have their own section)."""
    pick = [r for kind in ("decision", "fact", "question") for r in records if r.kind == kind and r.check != "needs_review"][:6]
    out = []
    for r in pick:
        quote = " ".join(r.evidence[0].quote.split())
        text = {"decision": f'Possible decision (not confirmed by the final check): "{quote}"', "fact": f'{" ".join(str(r.attrs.get("label", "")).split())}: {" ".join(str(r.attrs.get("value", "")).split())}' if r.attrs.get("value") else f'"{quote}"'}.get(r.kind, f'Question raised: "{quote}"')
        out.append(Claim(text=text, record_ids=[r.rid]))
    return out


def review(meter: Meter, records: list[Record], topics: list[Topic], units: list[Unit]) -> tuple[list[Claim], Claim | None, dict[str, list[Claim]], dict[str, Claim], list[str]]:
    """Runs the call and applies its output. Returns (summary, headline, per-speaker summaries, decision context, warnings). Raises PipelineError if the call fails (the caller falls back)."""
    user, listed, checked = build_input(records, topics, units)
    data = ask(meter, "review", "review", user, settings.review_model, settings.review_effort, "sum")
    apply_verdicts(listed, checked, data.get("v") if isinstance(data.get("v"), list) else [])
    summary = claims(data["sum"], listed)
    head = claims([data.get("head")], listed)
    for t in data.get("topics") if isinstance(data.get("topics"), list) else []:
        if isinstance(t, dict) and isinstance(t.get("t"), int) and not isinstance(t["t"], bool) and 0 <= t["t"] < len(topics):
            topics[t["t"]].bullets = claims(t.get("b"), listed)[:5]
            if t.get("o") in ("decided", "open", "info"):
                topics[t["t"]].outcome = t["o"]
    by_speaker: dict[str, list[Claim]] = {}
    owner = {r.rid: r.evidence[0].speaker for r in listed}
    for x in data.get("speakers") if isinstance(data.get("speakers"), list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("w"), str):
            continue
        mine = [c for c in claims(x.get("b"), listed) if all(owner.get(i) == x["w"] for i in c.record_ids)]  # every cited record must be this speaker's
        if mine and x["w"] not in by_speaker:
            by_speaker[x["w"]] = mine[:3]
    context: dict[str, Claim] = {}
    for x in data.get("ctx") if isinstance(data.get("ctx"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("n"), int) and not isinstance(x["n"], bool) and 0 <= x["n"] < len(listed) and listed[x["n"]].kind == "decision":
            got = claims([x], listed)
            if got and listed[x["n"]].rid in got[0].record_ids:  # it must cite the decision it explains
                context.setdefault(listed[x["n"]].rid, got[0])
    warnings = []
    scope = kinds_in_scope(settings.review_scope)
    unchecked = [r for r in records if r.kind in scope and r.check == "code"]
    if unchecked:
        warnings.append(f"{len(unchecked)} decision/action record(s) did not get a model verdict (see their notes, or the review limit of {MAX_CHECKED}); they are backed by verbatim quotes only.")
    return summary, (head[0] if head else None), by_speaker, context, warnings
