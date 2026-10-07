"""Everything here is plain code over the checked Result (no model calls): per-speaker summary, tasks by owner, decision timeline,
checklist, email text and calendar file."""
import re
from datetime import date, datetime, timedelta, timezone

from app.core.report import mmss, names, owner_of, who
from app.core.schemas import Record, Result

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
MON = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


# ------------------------------------------------------------------------------------------------ speakers
def speaker_summary(r: Result) -> list[dict]:
    """Per speaker label: talk time, words, and what they did in the checked records. Empty if speakers are unknown."""
    nm = names(r)
    stats: dict[str, dict] = {}
    total = sum(max(0.0, s.end - s.start) for s in r.refined.segments if s.speaker_known) or 1.0
    for s in r.refined.segments:
        if not s.speaker_known:
            continue
        a = stats.setdefault(s.speaker, {"speaker": s.speaker, "name": who(s.speaker, nm), "role": r.speaker_roles.get(s.speaker), "summary": r.speaker_summaries.get(s.speaker, []), "seconds": 0.0, "words": 0, "lines": 0,
                                         "commitments": [], "questions": [], "proposals": [], "risks": []})
        a["seconds"] += max(0.0, s.end - s.start)
        a["words"] += len(s.text.split())
        a["lines"] += 1
    for rec in r.records:
        sp = rec.evidence[0].speaker
        a = stats.get(sp)
        if a is None:
            continue
        if rec.kind == "action" and rec.attrs["owner"]["basis"] == "first_person" and rec.attrs["owner"]["speaker"] == sp:
            a["commitments"].append(rec)
        elif rec.kind == "question":
            a["questions"].append(rec)
        elif rec.kind == "proposal":
            a["proposals"].append(rec)
        elif rec.kind == "risk":
            a["risks"].append(rec)
    out = sorted(stats.values(), key=lambda x: -x["seconds"])
    for a in out:
        a["share"] = round(100 * a["seconds"] / total)
    return out


# ------------------------------------------------------------------------------------------------ tasks and decisions
def tasks_by_owner(r: Result) -> list[tuple[str, list[Record]]]:
    """Actions grouped by owner; tasks with no stated owner come last."""
    nm = names(r)
    groups: dict[str, list[Record]] = {}
    for x in (x for x in r.records if x.kind == "action"):
        groups.setdefault(owner_of(x, nm), []).append(x)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "unspecified", kv[0].lower()))


def timeline(r: Result) -> list[Record]:
    """Decisions, proposals and disagreements in the order they came up."""
    return sorted((x for x in r.records if x.kind in ("decision", "proposal", "disagreement")), key=lambda x: x.start)


def checklist_md(r: Result) -> str:
    nm = names(r)
    lines = ["# Next steps", ""]
    for owner, recs in tasks_by_owner(r):
        lines.append(f"## {owner if owner != 'unspecified' else 'No owner stated'}")
        lines += [f"- [ ] {' '.join(x.text.split())}" + (f" (deadline: {x.attrs['deadline']})" if x.attrs.get("deadline") else "") + f" [{x.rid}]" for x in recs]
        lines.append("")
    if len(lines) == 2:
        lines.append("No action items were recorded in this meeting.")
    return "\n".join(lines).rstrip() + "\n"


def email_text(r: Result) -> str:
    """Plain text ready to paste into an email."""
    nm = names(r)
    out = [f"Subject: Meeting summary ({mmss(r.audio.duration_seconds)} meeting)", "", "Hello,", "", "Here is a short summary of the meeting.", ""]
    if r.headline:
        out += [" ".join(r.headline.text.split()), ""]
    if r.summary:
        out += ["SUMMARY"] + [f"- {' '.join(c.text.split())}" for c in r.summary] + [""]
    dec = [x for x in r.records if x.kind == "decision" and x.check != "needs_review"]
    if dec:
        out += ["DECISIONS"] + [f"- {' '.join(x.text.split())}" + ("" if x.status == "confirmed" else f" ({x.status})") for x in dec] + [""]
    acts = [x for x in r.records if x.kind == "action"]
    if acts:
        out += ["ACTION ITEMS"] + [f"- {' '.join(x.text.split())} | owner: {owner_of(x, nm)} | deadline: {x.attrs.get('deadline') or 'unspecified'}" for x in acts] + [""]
    qs = [x for x in r.records if x.kind == "question" and x.status == "open"]
    if qs:
        out += ["OPEN QUESTIONS"] + [f"- {' '.join(x.text.split())}" for x in qs] + [""]
    if r.status == "partial":
        out += ["Note: this record is incomplete; some processing steps did not finish.", ""]
    out += ["Best regards"]
    return "\n".join(out) + "\n"


def attention(r: Result) -> list[str]:
    """Things a reader should look at, counted from the checked records (no model)."""
    acts = [x for x in r.records if x.kind == "action"]
    out = []
    n = sum(1 for a in acts if a.attrs["owner"]["basis"] == "unspecified")
    if n:
        out.append(f"{n} of {len(acts)} action item(s) have no stated owner (shown as unspecified).")
    n = sum(1 for a in acts if not a.attrs.get("deadline"))
    if n:
        out.append(f"{n} of {len(acts)} action item(s) have no stated deadline (shown as unspecified).")
    n = sum(1 for x in r.records if x.kind == "question" and x.status == "open")
    if n:
        out.append(f"{n} question(s) were left open.")
    n = sum(1 for x in r.records if x.kind == "disagreement" and x.status == "unresolved")
    if n:
        out.append(f"{n} disagreement(s) were not resolved.")
    n = sum(1 for x in r.records if x.kind == "proposal" and x.status == "proposed")
    if n:
        out.append(f"{n} proposal(s) were made without a recorded outcome.")
    n = sum(1 for x in r.records if x.check == "needs_review")
    if n:
        out.append(f"{n} record(s) were doubted by the review step and need a human look.")
    if not any(x.kind == "decision" for x in r.records):
        out.append("No decision was recorded in this meeting.")
    return out


# ------------------------------------------------------------------------------------------------ deadlines and calendar
def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def resolve_deadline(text: str, meeting: date | None) -> tuple[date, str] | None:
    """A calendar date for a stated deadline, or None. Never guesses: a missing year needs the meeting date, a weekday needs it too,
    and vague phrases ("soon", "next week", "next Friday", "end of month") are not resolved."""
    t = " ".join((text or "").lower().replace(",", " ").split())
    if not t or re.search(r"\bnext\b|\bend of\b|\bsoon\b|\bweek\b|\bmonth\b", t):
        return None
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        return (d, "stated date") if d else None
    for rx, order in ((rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? {MON}(?: (\d{{4}}))?\b", "dm"), (rf"\b{MON} (\d{{1,2}})(?:st|nd|rd|th)?(?: (\d{{4}}))?\b", "md")):
        if m := re.search(rx, t):
            day, mon, year = (int(m[1]), MONTHS[m[2][:3]], m[3]) if order == "dm" else (int(m[2]), MONTHS[m[1][:3]], m[3])
            y = int(year) if year else (meeting.year if meeting else None)
            d = _valid(y, mon, day) if y else None
            return (d, "stated date" if year else "stated day and month, year from the meeting date") if d else None
    if meeting:
        if re.search(r"\btomorrow\b", t):
            return meeting + timedelta(days=1), "tomorrow, relative to the meeting date"
        if re.search(r"\btoday\b", t):
            return meeting, "today, relative to the meeting date"
        if m := re.search(rf"\b({'|'.join(WEEKDAYS)})\b", t):
            ahead = (WEEKDAYS[m[1]] - meeting.weekday()) % 7 or 7
            return meeting + timedelta(days=ahead), f"first {m[1].title()} after the meeting date"
    return None


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(r: Result) -> tuple[str, int, int]:
    """(calendar text, events written, deadlines that could not be turned into a date). All-day events, one per task with a resolvable deadline."""
    try:
        meeting = date.fromisoformat(r.meeting_date) if r.meeting_date else None
    except ValueError:
        meeting = None
    nm = names(r)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ev, skipped = [], 0
    for x in (x for x in r.records if x.kind == "action" and x.attrs.get("deadline")):
        res = resolve_deadline(x.attrs["deadline"], meeting)
        if res is None:
            skipped += 1
            continue
        d, how = res
        desc = f"Owner: {owner_of(x, nm)}. Deadline as said in the meeting: '{x.attrs['deadline']}' ({how}). Quote: \"{' '.join(x.evidence[0].quote.split())}\""
        ev += ["BEGIN:VEVENT", f"UID:{x.rid}-{r.job_id}@meeting-lite", f"DTSTAMP:{now}", f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
               f"SUMMARY:{_esc(' '.join(x.text.split()))}", f"DESCRIPTION:{_esc(desc)}", "END:VEVENT"]
    body = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//meeting-lite//EN", *ev, "END:VCALENDAR"]
    return "\r\n".join(body) + "\r\n", len(ev) // 8, skipped
