"""Where the system is unsure, listed with a severity. Everything here is computed from what the pipeline really produced (the recogniser's own word
confidence, unusual words, unconfirmed suggestions, the checks' findings); nothing is made up and nothing is stored.

Severity: error (a step failed) > warning (something may be missing or wrong) > review (a human should look) > info (good to know).
"""
import re

from app.core import insights, session
from app.core.wording import plain
from app.core.pipeline import load_phase1, load_phase2, load_result
from app.core.report import label_name, mmss
from app.utils.errors import PipelineError

ORDER = {"error": 0, "warning": 1, "review": 2, "info": 3}
LOW_TERM_CONFIDENCE = 0.85
LOW_WORD_CONFIDENCE = 0.6  # a run of words the recogniser itself was unsure of
LOW_SEGMENT_LOGPROB = -1.0  # for recognisers that score lines, not words
NOT_TERMS = {"mhmm", "mmhmm", "mhm", "hmm", "uhhuh", "uhm", "umm", "yeah", "yep", "nope", "okay", "gonna", "wanna", "gotta", "kinda", "sorta", "lemme", "dunno"}  # speech noises and contractions are not mis-heard terms


def _issue(severity: str, stage: str, message: str, at: float | None = None, segment_id: str | None = None) -> dict:
    return {"severity": severity, "stage": stage, "message": message, "at": at, "segment_id": segment_id}


def _transcription(p1) -> list[dict]:
    out, last = [], -999.0
    for s in p1.transcript.segments:
        low = None
        words = [w for w in s.words if w.confidence is not None]
        if len(words) >= 3:
            for i in range(len(words) - 2):
                trio = words[i : i + 3]
                if sum(w.confidence for w in trio) / 3 < LOW_WORD_CONFIDENCE:
                    low = trio[0].start
                    break
        elif s.avg_logprob is not None and s.avg_logprob < LOW_SEGMENT_LOGPROB:
            low = s.start
        if low is not None and low - last > 8:
            out.append(_issue("review", "transcription", f"Possible transcription ambiguity at {mmss(low)}: the recogniser was unsure of these words.", low, s.segment_id))
            last = low
    if len(out) > 12:
        extra = len(out) - 12
        out = out[:12] + [_issue("info", "transcription", f"{extra} more passages with low recogniser confidence are not listed.")]
    conf: dict[str, float] = {}  # lowest recogniser confidence seen for each word
    for sg in p1.transcript.segments:
        for w in sg.words:
            if w.confidence is not None:
                k = re.sub(r"[^a-z']", "", w.text.lower())
                conf[k] = min(conf.get(k, 1.0), w.confidence)
    # An unusual word is only worth a look when the recogniser itself was unsure of it (or when it gives no confidence at all, then a few are listed)
    unusual = [r for r in p1.rare_words if r.word not in NOT_TERMS and (conf.get(r.word) is None or conf[r.word] < LOW_TERM_CONFIDENCE)]
    for r in unusual[:6]:
        out.append(_issue("review", "transcription", f"Unusual word \"{r.word}\" at {mmss(r.first_start)} may be a technical term or a mis-hearing.", r.first_start))
    if p1.transcript.speakers_known is False:
        out.append(_issue("warning", "speakers", "Speakers could not be told apart: this transcript has no speaker labels, so every line is attributed to an unknown speaker."))
    return out


def _speakers(p1, p2, overrides: dict[str, str]) -> list[dict]:
    out = []
    if not p1.transcript.speakers_known and not (p2 and p2.speaker_labels):
        return out
    labels = sorted({s.speaker for s in (p2.refined if p2 else p1.transcript).segments if s.speaker_known})
    named = {x.speaker for x in (p2.name_suggestions if p2 else []) if x.name and ((x.status == "applied" and x.user_decision != "reject") or x.user_decision == "accept")} | {k for k, v in overrides.items() if v}
    unnamed = [label_name(l) for l in labels if l not in named]
    if unnamed:
        out.append(_issue("info", "speakers", f"No name is known for {', '.join(unnamed)}. Names are only shown when the meeting itself shows them or you add them."))
    for x in (p2.name_suggestions if p2 else []):
        if x.status == "suggested" and x.user_decision is None and x.name:
            out.append(_issue("review", "speakers", f"A name was suggested for {label_name(x.speaker)} (\"{x.name}\", said to them at {mmss(x.start)}). Confirm it or leave the speaker unnamed.", x.start))
    return out


def _refinement(p2) -> list[dict]:
    if not p2:
        return []
    out = []
    pending = [e for e in p2.edits if e.status == "suggested" and e.user_decision is None]
    if pending:
        out.append(_issue("review", "refinement", f"{len(pending)} proposed correction(s) have no supporting evidence in the recording and need your decision."))
    blocked = [e for e in p2.edits if e.status == "rejected"]
    if blocked:
        out.append(_issue("info", "refinement", f"{len(blocked)} proposed correction(s) were blocked by the safety checks (they would have changed a number, date, time or negation, or were not exact)."))
    return out


def _classify(w: str) -> str:
    if re.search(r"could not run|did not run|could not be double-checked|could not be analysed|could not be verified|incomplete|shown as heard", w, re.I):
        return "warning"
    return "info"


def _report(r) -> list[dict]:
    out = []
    for m in insights.attention(r):
        sev = "warning" if "doubted" in m else "review" if "disagreement" in m else "info"
        out.append(_issue(sev, "report", m))
    for w in r.warnings:
        if w.startswith("Speaker labels were inferred"):
            continue
        out.append(_issue(_classify(plain(w)), "report", plain(w)))
    return out


def collect(job_id: str) -> list[dict]:
    """Every issue for a meeting, most severe first."""
    s = session.load(job_id) or session.derive(job_id)
    out: list[dict] = []
    for k, st in s["stages"].items():
        if st["state"] == "failed" and st.get("error"):
            e = st["error"]
            out.append(_issue("error", k, f"{e['title']} {e['action']}"))
    try:
        p1 = load_phase1(job_id)
    except PipelineError:
        return out
    p2 = load_phase2(job_id)
    overrides = {}
    try:
        from app.core.corrections import load_overrides

        overrides = load_overrides(job_id)
    except Exception:  # noqa: BLE001 - overrides are optional
        pass
    out += _transcription(p1) + _speakers(p1, p2, overrides) + _refinement(p2)
    try:
        out += _report(load_result(job_id))
    except PipelineError:
        pass
    return sorted(out, key=lambda i: (ORDER[i["severity"]], i["at"] if i["at"] is not None else 1e9))
