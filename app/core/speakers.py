"""Speaker labels from the TEXT alone (for providers that give none, such as Groq Whisper), using one model call per window.

This is an inference from conversation cues (questions and answers, being addressed by name, introductions), not voice recognition, so the labels
are flagged as inferred everywhere. Code only accepts well-formed turns; anything unsure stays "unknown speaker".
"""
import re
from collections.abc import Callable

from app.config.settings import settings
from app.core.gates import ground_people
from app.core.llm import EFFORT_TOKENS, Meter, ask, est_tokens, prompt_text
from app.core.schemas import NameSuggestion
from app.core.units import build_units, make_windows
from app.schemas.transcript import Transcript
from app.utils.errors import PipelineError

OVERLAP = 4  # earlier lines shown (with their labels) so letters stay consistent across windows
WHO = re.compile(r"^[A-H?]$")
INFERRED_NOTE = ("Speaker labels were inferred from the text by a language model, not from voices. They can be wrong, and owners taken from them "
                 "('I will do it') are less certain. Choose the deepgram provider to separate speakers by voice instead.")


def _lines(t: Transcript) -> list[str]:
    return [f"{i + 1} {s.text}" for i, s in enumerate(t.segments)]


def windows(t: Transcript) -> list[tuple[int, int]]:
    """(first line index, end index) ranges of about `window_chars` characters."""
    lines, out, i = _lines(t), [], 0
    while i < len(lines):
        j, used = i, 0
        while j < len(lines) and (j == i or used + len(lines[j]) + 1 <= settings.window_chars):
            used += len(lines[j]) + 1
            j += 1
        out.append((i, j))
        i = j
    return out


def plan(t: Transcript) -> int:
    """Expected token spend, computed before any call."""
    return sum(est_tokens(prompt_text("speakers")) + int(sum(len(x) + 1 for x in _lines(t)[a:b]) / 3.6) + 150 + EFFORT_TOKENS.get(settings.speakers_effort, 1500) // 3 for a, b in windows(t))


def apply(t: Transcript, labels: dict[str, str]) -> Transcript:
    """Same segments (ids, times, text); only the speaker label of labelled lines changes."""
    segs = [s.model_copy(update={"speaker": labels[s.segment_id], "speaker_known": True}) if s.segment_id in labels else s for s in t.segments]
    return t.model_copy(update={"segments": segs, "speakers_known": bool(labels)})


def label(meter: Meter, t: Transcript, run: Callable, say: Callable) -> tuple[dict[str, str], list[str], int]:
    """Returns ({segment_id: "SPEAKER_A"}, warnings, failed windows). `run(what, fn)` runs one model call with the pipeline's retry rules."""
    lines, wins = _lines(t), windows(t)
    letters: dict[int, str] = {}
    seen: list[str] = []
    warnings: list[str] = []
    failed = 0
    for n, (a, b) in enumerate(wins, 1):
        say("speakers", f"Working out who is speaking ({n} of {len(wins)})")
        parts = [f"SPEAKERS SO FAR: {', '.join(seen) or 'none'}"]
        if a:
            parts.append("CONTEXT (already labelled, do not label):\n" + "\n".join(f"{lines[k].split(' ', 1)[0]} [{letters.get(k, '?')}] {t.segments[k].text}" for k in range(max(0, a - OVERLAP), a)))
        parts.append("TRANSCRIPT:\n" + "\n".join(lines[a:b]))
        try:
            data = run(f"w{n}", lambda: ask(meter, "speakers", "speakers", "\n\n".join(parts), settings.speakers_model, settings.speakers_effort, "turns", window=f"w{n}"))
        except PipelineError as e:
            failed += 1
            warnings.append(f"Speaker labelling of window w{n} did not run ({e.code}: {e.message[:160]}); those lines stay 'unknown speaker'.")
            if e.code in ("TOKEN_BUDGET", "RUN_TIMEOUT", "CALL_LIMIT"):
                break
            continue
        turns: dict[int, str] = {}
        for x in data["turns"]:
            if isinstance(x, dict) and isinstance(x.get("at"), int) and not isinstance(x["at"], bool) and a < x["at"] <= b and isinstance(x.get("who"), str):
                who = x["who"].strip().upper()
                if WHO.match(who):
                    turns.setdefault(x["at"] - 1, who)
        ordered = sorted(turns.items())
        for k, (start, who) in enumerate(ordered):
            end = ordered[k + 1][0] if k + 1 < len(ordered) else b
            if who != "?":
                if who not in seen:
                    seen.append(who)
                for i in range(start, end):
                    letters[i] = who
    if len(set(letters.values())) < 2:
        if not failed:
            warnings.append("The model could not tell the speakers apart from the text alone; all lines stay 'unknown speaker'.")
        return {}, warnings, failed
    labels = {t.segments[i].segment_id: f"SPEAKER_{w}" for i, w in letters.items()}
    if len(labels) < len(lines):
        warnings.append(f"{len(lines) - len(labels)} of {len(lines)} line(s) were left as 'unknown speaker' because the model was unsure.")
    return labels, warnings, failed


# ------------------------------------------------------------------------------------------------ who is who
def plan_names(t: Transcript) -> int:
    units, _ = build_units(t)
    return sum(est_tokens(prompt_text("names")) + int(w.chars / 3.6) + 150 + EFFORT_TOKENS.get(settings.names_effort, 700) // 2
               for w in make_windows(units, settings.window_chars, settings.window_overlap_units))


def propose_people(meter: Meter, t: Transcript, run: Callable, say: Callable) -> tuple[list[NameSuggestion], list[str], int]:
    """Who each speaker label seems to be (name and/or role), from what is said in the meeting. Needs speaker labels (from the provider or from `label`)."""
    if not any(s.speaker_known for s in t.segments):
        return [], [], 0
    units, legend = build_units(t)
    windows_ = make_windows(units, settings.window_chars, settings.window_overlap_units)
    found: list[dict] = []
    warnings: list[str] = []
    failed = 0
    for w in windows_:
        say("names", f"Finding who the speakers are ({w.wid} of {len(windows_)})")
        head = f"SPEAKERS: {', '.join(sorted(legend))}\n\n"
        try:
            data = run(w.wid, lambda w=w: ask(meter, "names", "names", head + w.render(), settings.names_model, settings.names_effort, "people", window=w.wid))
        except PipelineError as e:
            failed += 1
            warnings.append(f"Finding the speakers' names in {w.wid} did not run ({e.code}: {e.message[:160]}).")
            if e.code in ("TOKEN_BUDGET", "RUN_TIMEOUT", "CALL_LIMIT"):
                break
            continue
        found += ground_people(data["people"], {u.uid: u for u in w.context + w.target}, legend, {u.uid for u in w.target})
    found.sort(key=lambda x: x["basis"] == "addressed")  # a self-introduction beats a name said by someone else
    seen_label, seen_name, seen_role, out = {}, set(), set(), []
    for x in found:
        name = x["name"] if x["name"] and x["speaker"] not in seen_label and x["name"].lower() not in seen_name else None
        role = x["role"] if x["role"] and x["speaker"] not in seen_role else None
        if not name and not role:
            continue
        if name:
            seen_label[x["speaker"]] = name
            seen_name.add(name.lower())
        if role:
            seen_role.add(x["speaker"])
        out.append(NameSuggestion(suggestion_id=f"who_{len(out) + 1:03d}", speaker=x["speaker"], name=name, role=role, basis=x["basis"], quote=x["quote"], uid=x["uid"],
                                  start=x["start"], status="applied" if x["basis"] == "self" else "suggested"))
    return out, warnings, failed
