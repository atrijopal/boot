"""Three phases, each saved to disk and runnable on its own:

Phase 1: transcript (speech-to-text API) + free extractions (numbers, dates, names, acronyms, ...). No model tokens.
Phase 2: refinement. The model proposes fixes for mis-heard terms; code gates them. One call per window.
Phase 3: minutes. At most `PHASE3_MAX_CALLS` (3) model calls: extraction (1 or 2 windows) + one review/summary call. Report built by code.
"""
import difflib
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from app.asr.registry import get_provider
from app.audio.prepare import prepare_audio
from app.audio.validate import validate_file
from app.config.settings import settings
from app.core import echoes, report, speakers
from app.core.facts import extract_facts, rare_words, speaker_stats
from app.core.gates import apply_edits, gate_edits, ground_names, ground_record, protected_spans, suspect_hints
from app.core.lenses import LENS_ORDER, LENSES
from app.core.llm import EFFORT_TOKENS, Meter, est_tokens, prompt_text, usage_of
from app.core.review import extractive, review
from app.core.schemas import Phase1, Phase2, Record, Result, Topic
from app.core.steps import EXTRACT_PROMPTS, FOCUS_TEXT, LENS_SETS, extract, refine, verify_edits
from app.core.units import build_units, make_windows
from app.preprocessing.normalize import normalize_transcript
from app.schemas.refinement import Evidence as EditEvidence
from app.schemas.transcript import Transcript
from app.utils import cache as cache_io
from app.utils.atomic import atomic_write
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("pipeline")
StatusFn = Callable[[str, str], None]
_W = re.compile(r"[a-z0-9]+")
RETRY_WAIT = 30  # seconds before the one retry of a rate-limited window


def _norm(s: str) -> str:
    return " ".join(_W.findall(s.lower()))


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def plan_refine(windows) -> int:
    """Expected spend of phase 2, computed BEFORE any model call."""
    return sum(est_tokens(prompt_text("refine")) + int(w.chars / 3.6) + 150 + EFFORT_TOKENS.get(settings.refine_effort, 1500) // 3 for w in windows)


def _task_tokens(w, focus: str) -> int:
    return est_tokens(prompt_text(EXTRACT_PROMPTS[focus])) + int(w.chars / 3.6) + 150 + EFFORT_TOKENS.get(settings.extract_effort, 1500)


def plan_extract(tasks) -> int:
    """Expected spend of phase 3: every extraction call (window, focus) plus the review call."""
    return sum(_task_tokens(w, f) for w, f in tasks) + est_tokens(prompt_text("review")) + 2500 + EFFORT_TOKENS.get(settings.review_effort, 700) + 1200


def capped_windows(units, max_windows: int):
    """Windows of the configured size; if that would need more than `max_windows` calls, the windows grow until it fits."""
    size = settings.window_chars
    while True:
        ws = make_windows(units, size, settings.window_overlap_units)
        if len(ws) <= max_windows:
            return ws
        size = int(size * 1.25) + 1


def _retry(meter: Meter, say: StatusFn, what: str, fn):
    """Runs one model call; a rate-limited call gets ONE patient second try, and a call whose JSON the provider could not generate gets one immediate retry."""
    try:
        return fn()
    except PipelineError as first:
        if "json_validate_failed" in first.message and time.monotonic() < meter.deadline:
            say("wait", f"A step returned an unreadable answer; trying it again ({what})")
            return fn()
        if first.code not in ("API_RATE_LIMITED", "API_UNAVAILABLE") or time.monotonic() + RETRY_WAIT > meter.deadline:
            raise
        say("wait", f"The service is busy; trying again in {RETRY_WAIT} seconds ({what})")
        time.sleep(RETRY_WAIT)
        return fn()


def dedupe(records: list[Record]) -> tuple[list[Record], dict[int, Record]]:
    """Merge duplicates from overlapping windows. Returns (records, {id(original): kept record})."""
    records = sorted(records, key=lambda r: (r.start, r.kind))
    kept: list[Record] = []
    canon: dict[int, Record] = {}
    for r in records:
        dup = next((k for k in kept if k.kind == r.kind and abs(k.start - r.start) <= 90 and
                    (_sim(k.text, r.text) >= 0.8 or ({e.uid for e in k.evidence} & {e.uid for e in r.evidence} and _sim(k.text, r.text) >= 0.55))
                    and (r.kind != "fact" or k.attrs.get("value") == r.attrs.get("value"))), None)
        if dup is None:
            kept.append(r)
            canon[id(r)] = r
            continue
        have = {(e.uid, e.quote) for e in dup.evidence}
        dup.evidence += [e for e in r.evidence if (e.uid, e.quote) not in have][: max(0, 6 - len(dup.evidence))]
        if dup.kind == "action" and dup.attrs["owner"]["basis"] == "unspecified" and r.attrs["owner"]["basis"] != "unspecified":
            dup.attrs["owner"] = r.attrs["owner"]
        canon[id(r)] = dup
    return kept, canon


def _assign_ids(records: list[Record]) -> None:
    n: dict[str, int] = {}
    for r in sorted(records, key=lambda r: (LENS_ORDER.index(r.kind), r.start)):
        n[r.kind] = n.get(r.kind, 0) + 1
        r.rid = f"{LENSES[r.kind]['prefix']}_{n[r.kind]:03d}"


def _quote_of(item) -> str:
    """The quote(s) the model gave for a proposed record, for the 'why dropped' table."""
    qs = item.get("q") if isinstance(item, dict) else None
    return " | ".join(str(q[1])[:120] for q in qs if isinstance(q, (list, tuple)) and len(q) == 2)[:300] if isinstance(qs, list) else ""


def _job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise PipelineError("BAD_JOB_ID", "Invalid job identifier.")
    return Path(settings.outputs_dir) / job_id


def _load(job_id: str, name: str, model, missing_code: str, missing_msg: str, required: bool = True):
    f = _job_dir(job_id) / name
    if not f.is_file():
        if required:
            raise PipelineError(missing_code, missing_msg)
        return None
    return model.model_validate_json(f.read_text(encoding="utf-8"))


def load_phase1(job_id: str) -> Phase1:
    return _load(job_id, "phase1.json", Phase1, "JOB_NOT_FOUND", "No phase 1 result for this job. Upload the recording first.")


def load_phase2(job_id: str) -> Phase2 | None:
    return _load(job_id, "phase2.json", Phase2, "", "", required=False)


def _meeting_date(job_id: str) -> str | None:
    """The date the user typed at upload (YYYY-MM-DD), if any."""
    try:
        v = json.loads((_job_dir(job_id) / "upload.json").read_text(encoding="utf-8")).get("meeting_date")
        return v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
    except (OSError, ValueError):
        return None


def _txt(t: Transcript) -> str:
    from app.core.report import label_name

    return "\n".join(f"[{s.start:7.2f}-{s.end:7.2f}] {s.speaker_name + ' (' + label_name(s.speaker) + ')' if s.speaker_name else label_name(s.speaker)}: {s.text}" for s in t.segments)


# ------------------------------------------------------------------------------------------------ phase 1
def run_phase1(path: str | Path, job_id: str | None = None, provider_name: str | None = None, status: StatusFn | None = None) -> Phase1:
    """Speech-to-text API + extractions that cost no model tokens."""
    say = status or (lambda s, m="": None)
    job_id = job_id or uuid.uuid4().hex[:12]
    timings: dict[str, float] = {}
    out_dir = _job_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    say("audio", "Checking and preparing the audio")
    p = validate_file(path)
    prepared = prepare_audio(p)
    meta = prepared.metadata
    provider = get_provider(provider_name)
    cached = cache_io.load(meta.sha256, provider.name, provider.model)
    if cached:
        say("transcribe", "Reusing the saved transcript (no API call)")
        raw = Transcript.model_validate(cached["transcript"])
        raw_payload = cached["raw"]
    else:
        say("transcribe", f"Transcribing with {provider.name} ({provider.model})")
        res = provider.transcribe(p, prepared.samples, prepared.sample_rate, lambda m: say("transcribe", m))
        raw, raw_payload = res.transcript, res.raw
        cache_io.store(meta.sha256, provider.name, provider.model, {"transcript": raw.model_dump(), "raw": raw_payload, "chunks": res.chunks})
    if not raw.segments or not raw.full_text().strip():
        raise PipelineError("NO_SPEECH", "No speech was detected in this recording.")
    t = normalize_transcript(raw)

    timings["transcribe"] = round(time.perf_counter() - t0, 2)

    t1 = time.perf_counter()
    say("extract_facts", "Extracting numbers, dates, names, acronyms and rare words")
    facts = extract_facts(t)
    timings["facts"] = round(time.perf_counter() - t1, 2)
    notes = list(raw.notes)
    if not raw.speakers_known:
        notes.append("Groq Whisper gives text only: it cannot recognise voices, so every line is 'unknown speaker'. Choose deepgram to separate speakers by voice (Speaker 1, Speaker 2, ...); phase 2 can otherwise only guess from the text.")
    p1 = Phase1(job_id=job_id, provider=provider.name, asr_model=raw.model, audio=meta, transcript=t, facts=facts, rare_words=rare_words(t),
                speakers=speaker_stats(t), notes=notes, timings_seconds=timings)
    atomic_write((out_dir / "phase1.json"), p1.model_dump_json(indent=1))
    atomic_write((out_dir / "raw_transcript.txt"), _txt(t))  # the speech-to-text result before any language-model step; never overwritten
    atomic_write((out_dir / "asr_raw.json"), json.dumps(raw_payload, ensure_ascii=False))
    log.info("job %s phase 1 done: %d segments", job_id, len(t.segments))
    return p1


# ------------------------------------------------------------------------------------------------ phase 2
def run_phase2(job_id: str, status: StatusFn | None = None, only: str | None = None) -> Phase2:
    """Speakers and refinement. `only="names"` runs just the speaker step (who is who), `only="refine"` just the refinement (it reads the speaker step's saved result);
    no argument runs both. Model calls: one per window per step; code gates every result."""
    say = status or (lambda s, m="": None)
    p1 = load_phase1(job_id)
    t0 = p1.transcript
    out_dir = _job_dir(job_id)
    prev = load_phase2(job_id) if only == "refine" else None
    meter = Meter()
    warnings: list[str] = []
    do_speakers = settings.speakers_enabled and not t0.speakers_known and only != "refine"
    do_names = settings.names_enabled and only != "refine"
    do_refine = settings.refine_enabled and only != "names"
    t1 = time.perf_counter()
    labels, edits, failed = dict(prev.speaker_labels) if prev else {}, [], 0
    suggestions = list(prev.name_suggestions) if prev else []
    t = speakers.apply(t0, labels) if labels else t0
    if not (do_speakers or do_refine or do_names):
        p2 = prev or Phase2(job_id=job_id, status="skipped", refined=apply_edits(t0, [], {}), warnings=["This step is switched off."])
    else:
        units0, _ = build_units(t0)
        plan = (speakers.plan(t0) if do_speakers else 0) + (plan_refine(make_windows(units0, settings.window_chars, settings.window_overlap_units)) if do_refine else 0) + (speakers.plan_names(t0) if do_names else 0)
        if plan > settings.max_run_tokens:
            raise PipelineError("TOO_LONG_FOR_BUDGET", f"This step needs about {plan} tokens but MAX_RUN_TOKENS is {settings.max_run_tokens}. Raise the limit or use a shorter recording.")
        if do_speakers:
            labels, w, f = speakers.label(meter, t0, lambda what, fn: _retry(meter, say, what, fn), say)
            warnings += w
            failed += f
            if labels:
                t = speakers.apply(t0, labels)
                warnings.append(speakers.INFERRED_NOTE)
        if do_names:
            suggestions, w, f = speakers.propose_people(meter, t, lambda what, fn: _retry(meter, say, what, fn), say)
            warnings += w
            failed += f
        if do_refine:
            units, legend = build_units(t)
            windows = make_windows(units, settings.window_chars, settings.window_overlap_units)
            protected = protected_spans(t)
            hints = suspect_hints(t, units)
            echo_hints = echoes.find(units)
            raw_edits: list = []
            for w in windows:
                say("refine", f"Refining the transcript ({w.wid} of {len(windows)})")
                try:
                    found = _retry(meter, say, w.wid, lambda w=w: refine(meter, w, hints, legend, echo_hints))
                except PipelineError as e:
                    failed += 1
                    warnings.append(f"Refinement of {w.wid} did not run ({e.code}: {e.message[:160]}); that part of the transcript is unrefined.")
                    if e.code in ("TOKEN_BUDGET", "RUN_TIMEOUT"):
                        break
                    continue
                ids = {u.uid for u in w.target}
                raw_edits += [e for e in found if e.get("u") in ids]
            seen = set()
            for e in gate_edits(raw_edits, {u.uid: u for u in units}, t, protected):
                key = (e.segment_id, e.find, e.replace)
                if key not in seen:
                    seen.add(key)
                    edits.append(e)
            for i, e in enumerate(edits, 1):
                e.edit_id = f"edt_{i:03d}"
            cand = [e for e in edits if e.status == "suggested" and (e.similarity or 0) >= settings.refine_min_similarity] if settings.verify_edits_enabled else []
            if cand:
                say("refine", "Checking the proposed corrections a second time")
                seg_by_id = {sg.segment_id: k for k, sg in enumerate(t.segments)}
                items = []
                for n, e in enumerate(cand):
                    k = seg_by_id[e.segment_id]
                    around = " / ".join(t.segments[j].text[:160] for j in (k - 1, k + 1) if 0 <= j < len(t.segments))
                    items.append({"i": n, "heard": e.find, "proposed": e.replace, "line": t.segments[k].text[:300], "around": around})
                try:
                    ok = _retry(meter, say, "the second check", lambda: verify_edits(meter, items))
                    for n, e in enumerate(cand):
                        if ok.get(n):
                            e.status = "applied"
                            e.support.append(EditEvidence(kind="second_check", detail="Confirmed by an independent second check"))
                except PipelineError as e_:
                    warnings.append(f"The second check of the proposed corrections did not run ({e_.code}); they stay as suggestions for you to decide.")
        calls = (list(prev.calls) if prev else []) + meter.calls
        for i, c in enumerate(calls, 1):
            c.n = i
        p2 = Phase2(job_id=job_id, status="partial" if failed or (prev and prev.status == "partial") else "complete", refined=apply_edits(t, edits, {}), edits=edits,
                    name_suggestions=suggestions, speaker_labels=labels, speakers_inferred=bool(labels), calls=calls, usage=usage_of(calls),
                    warnings=list(dict.fromkeys((prev.warnings if prev else []) + warnings)))
    p2.timings_seconds = {**(prev.timings_seconds if prev else {}), "names" if only == "names" else "refine" if only == "refine" else "phase2": round(time.perf_counter() - t1, 2)}
    atomic_write((out_dir / "phase2.json"), p2.model_dump_json(indent=1))
    atomic_write((out_dir / "refined_transcript.txt"), _txt(p2.refined))
    log.info("job %s phase 2 (%s) done: %d speaker labels, %d edits, %d calls", job_id, only or "all", len(labels), len(edits), p2.usage.calls)
    return p2


# ------------------------------------------------------------------------------------------------ phase 3
def run_phase3(job_id: str, status: StatusFn | None = None) -> Result:
    """Minutes: extraction (max 2 windows) + one review call = at most `phase3_max_calls` model calls. The report is built by code."""
    say = status or (lambda s, m="": None)
    p1 = load_phase1(job_id)
    p2 = load_phase2(job_id)
    out_dir = _job_dir(job_id)
    warnings: list[str] = []
    t = speakers.apply(p1.transcript, p2.speaker_labels) if p2 and p2.speaker_labels else p1.transcript  # with the inferred speaker labels, if any
    if p2 is None:
        warnings.append("Phase 2 (refinement) was not run; the minutes are extracted from the transcript as heard.")
    from app.core.corrections import effective_edits, load_overrides, merge_names, set_names  # imported here: corrections imports this module

    edits = effective_edits(p2) if p2 else []  # as the user left them (accepted / rejected fixes)
    warnings += p2.warnings if p2 else []
    refined_text = p2.refined if p2 else apply_edits(t, [], {})
    meter = Meter(max_calls=settings.phase3_max_calls)

    units, legend = build_units(refined_text)
    by_id = {u.uid: u for u in units}
    one_window = make_windows(units, settings.window_chars, settings.window_overlap_units)
    cap = settings.phase3_max_calls
    size = next((n for n in (5, 2) if n <= settings.extraction_lenses and n * len(one_window) + 1 <= cap), 1)  # the richest reader set that fits the call cap
    windows = one_window if size > 1 else capped_windows(units, max(1, cap - 1))  # one call is kept for the review
    tasks = [(w, f) for w in windows for f in LENS_SETS[size]]
    biggest = max(_task_tokens(w, f) for w, f in tasks)
    if biggest > settings.max_call_tokens:
        raise PipelineError("TOO_LONG_FOR_ONE_CALL", f"With at most {settings.phase3_max_calls} model calls, each extraction call would need about {biggest} tokens, above MAX_CALL_TOKENS "
                            f"({settings.max_call_tokens}; Groq's free tier allows 8,000 per minute per model). Raise PHASE3_MAX_CALLS or MAX_CALL_TOKENS, or use a shorter recording.")
    plan = plan_extract(tasks)
    if plan > settings.max_run_tokens:
        raise PipelineError("TOO_LONG_FOR_BUDGET", f"Phase 3 needs about {plan} tokens ({len(tasks)} extraction call(s) plus the review) but MAX_RUN_TOKENS is {settings.max_run_tokens}. Raise the limit or use a shorter recording.")

    # ---- extraction (model proposes records, code keeps only what the transcript supports)
    raw_records: list[Record] = []
    names, topics_raw, failed_windows, dropped_log = [], [], 0, []
    t2 = time.perf_counter()
    for w, focus in tasks:
        label = w.wid if focus == "all" else f"{w.wid}-{focus}"
        say("extract", f"Extracting {FOCUS_TEXT[focus]} ({label}, {len(w.target)} lines)")
        try:
            data = _retry(meter, say, label, lambda w=w, focus=focus, label=label: extract(meter, w, legend, focus, label))
        except PipelineError as e:
            failed_windows += 1
            warnings.append(f"Extraction {label} could not run ({e.code}: {e.message[:200]}); {'that part of the meeting' if focus == 'all' else 'that kind of record'} is missing from the records.")
            if e.code in ("TOKEN_BUDGET", "RUN_TIMEOUT", "CALL_LIMIT"):
                break
            continue
        target_ids = {u.uid for u in w.target}
        by_win = {u.uid: u for u in w.context + w.target}
        dropped = 0
        for raw_item in data["items"]:
            rec, why = ground_record(raw_item, units, by_win, target_ids, refined_text)
            if rec:
                raw_records.append(rec)
            else:
                dropped += 1
                dropped_log.append({"window": label, "quote": _quote_of(raw_item), "kind": str(raw_item.get("t", "")) if isinstance(raw_item, dict) else "", "text": str(raw_item.get("x", "") if isinstance(raw_item, dict) else raw_item)[:200], "reason": why})
        if dropped:
            warnings.append(f"{label}: {dropped} of {len(data['items'])} proposed record(s) dropped by the quote/number checks.")
        n, nw = ground_names(data["names"], by_win, legend)
        names += n
        warnings += nw
        topics_raw.append((w, data["topics"]))
    timings = {"extract": round(time.perf_counter() - t2, 2)}
    if failed_windows == len(tasks):
        raise PipelineError("ANALYSIS_FAILED", "The model could not extract anything from the recording. " + (warnings[-1] if warnings else ""))

    records, _ = dedupe(raw_records)
    _assign_ids(records)
    topics: list[Topic] = []
    for w, raw_topics in topics_raw:
        ids = [u.uid for u in w.target]
        for tp in raw_topics:
            if isinstance(tp, dict) and tp.get("a") in ids and tp.get("b") in ids and ids.index(tp["a"]) <= ids.index(tp["b"]) and str(tp.get("n") or "").strip():
                a, b = by_id[tp["a"]], by_id[tp["b"]]
                title = " ".join(str(tp["n"]).split())[:80]
                if topics and _sim(topics[-1].title, title) >= 0.75:
                    topics[-1] = topics[-1].model_copy(update={"end": b.end, "last_uid": b.uid})
                else:
                    topics.append(Topic(title=title, start=a.start, end=b.end, first_uid=a.uid, last_uid=b.uid))
    topics.sort(key=lambda x: x.start)

    # ---- review: check the records, write the summary and topic bullets (one call over records, not the transcript)
    summary, headline, by_speaker, dec_context, review_failed = [], None, {}, {}, False
    t3 = time.perf_counter()
    if records:
        say("review", "Reviewing the records and writing the summary")
        try:
            summary, headline, by_speaker, dec_context, w4 = _retry(meter, say, "the review", lambda: review(meter, records, topics, units))
            warnings += w4
        except PipelineError as e:
            review_failed = True
            for r in records:
                if r.kind == "decision":
                    r.notes.append("Not confirmed: the final check did not run, so this has only been matched to the recording.")
            warnings.append(f"The review step did not run ({e.code}: {e.message[:160]}). Records are checked by code only and the summary is restated from them.")
        if not summary:
            summary = extractive(records)
            if not review_failed:
                warnings.append("The model's summary sentences did not pass checking (or none were written); showing restated verified records instead.")
    timings["review"] = round(time.perf_counter() - t3, 2)

    refined = apply_edits(t, edits, {n.speaker: n.name for n in merge_names(p2, names)[0]})
    calls = list(p2.calls if p2 else []) + meter.calls
    for i, c in enumerate(calls, 1):
        c.n = i
    status_ = "partial" if (failed_windows or review_failed or (p2 and p2.status == "partial")) else "complete"
    meeting_date = _meeting_date(job_id)
    result = Result(job_id=job_id, status=status_, review_ran=not review_failed, meeting_date=meeting_date, speakers_inferred=bool(p2 and p2.speakers_inferred), provider=p1.provider, asr_model=p1.asr_model, audio=p1.audio, transcript=t, refined=refined, edits=edits,
                    records=records, topics=topics, headline=headline, summary=summary, decision_context=dec_context, speaker_summaries=by_speaker, speaker_names=names, warnings=list(dict.fromkeys(warnings)), dropped=dropped_log,
                    usage=usage_of(calls), calls=calls, timings_seconds={**p1.timings_seconds, **(p2.timings_seconds if p2 else {}), **timings})
    all_names, roles = merge_names(p2, names)  # phase-2 names first, then the extraction's; typed names win over both
    result = set_names(result.model_copy(update={"extracted_names": names}), all_names, roles, load_overrides(job_id))
    report.save(out_dir, result)
    log.info("job %s phase 3 done: %d records, %d calls, %d tokens", job_id, len(records), result.usage.calls, result.usage.total_tokens)
    return result


def run_pipeline(path: str | Path, job_id: str | None = None, provider_name: str | None = None, status: StatusFn | None = None) -> Result:
    """All three phases in a row (terminal use and tests)."""
    job_id = job_id or uuid.uuid4().hex[:12]
    run_phase1(path, job_id, provider_name, status)
    run_phase2(job_id, status)
    return run_phase3(job_id, status)


def load_result(job_id: str) -> Result:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise PipelineError("BAD_JOB_ID", "Invalid job identifier.")
    f = Path(settings.outputs_dir) / job_id / "result.json"
    if not f.is_file():
        raise PipelineError("JOB_NOT_FOUND", "No result for this job.")
    return Result.model_validate_json(f.read_text(encoding="utf-8"))
