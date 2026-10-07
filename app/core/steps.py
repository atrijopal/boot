"""The model calls of steps 2 and 3, one call per window. Step 4 (review) is in review.py. Speech-to-text (step 1) is not a model call."""
from app.config.settings import settings
from app.core.llm import Meter, ask
from app.core.units import Window


def _head(legend: dict[str, str]) -> str:
    return f"SPEAKERS: {', '.join(sorted(legend)) or 'unknown'}\n"


def refine(meter: Meter, w: Window, hints: list[tuple[str, str]], legend: dict[str, str], echoes: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """Step 2: proposed fixes for mis-heard words. Code decides which ones are applied."""
    ids = {u.uid for u in w.target}
    check = ", ".join(f"{uid} {word}" for uid, word in hints if uid in ids)
    again = "; ".join(f'{uid} "{first}" / "{later}"' for uid, first, later in (echoes or []) if uid in ids)
    head = _head(legend) + (f"CHECK (rare words, maybe mis-heard): {check}\n" if check else "") + (f"REPEATED (said again nearby, written differently; one of them may be a mis-hearing): {again}\n" if again else "") + "\n"
    data = ask(meter, "refine", "refine", head + w.render(), settings.refine_model, settings.refine_effort, "edits", window=w.wid)
    return [e for e in data["edits"] if isinstance(e, dict)]


EXTRACT_PROMPTS = {"all": "extract", "core": "extract_core", "context": "extract_context", "decisions": "extract_decisions", "actions": "extract_actions",
                   "issues": "extract_issues", "details": "extract_details", "discussion": "extract_discussion"}
LENS_SETS = {1: ("all",), 2: ("core", "context"), 5: ("decisions", "actions", "issues", "details", "discussion")}  # one combined reader, two, or five focused readers per window
FOCUS_TEXT = {"all": "decisions, actions and topics", "core": "decisions and tasks", "context": "questions, concerns, figures and topics", "decisions": "decisions and proposals",
              "actions": "tasks, owners and deadlines", "issues": "questions and concerns", "details": "figures, topics and names", "discussion": "the main discussion points"}


def model_for(focus: str) -> str:
    """The model that reads for this focus: its own setting for a focused reader, the general extraction model for the combined and two-way readers."""
    return getattr(settings, f"extract_model_{focus}", None) or settings.extract_model


def extract(meter: Meter, w: Window, legend: dict[str, str], focus: str = "all", label: str = "") -> dict:
    """Step 3: records, topics and speaker names from the refined transcript. focus: all | core (decisions, proposals, actions) | context (questions, risks, figures, topics, names)."""
    data = ask(meter, "extract", EXTRACT_PROMPTS[focus], _head(legend) + "\n" + w.render(), model_for(focus), settings.extract_effort, "items", window=label or w.wid)
    for k in ("topics", "names"):
        if not isinstance(data.get(k), list):
            data[k] = []
    return data


def verify_edits(meter: Meter, items: list[dict]) -> dict[int, bool]:
    """Independent second check of suggested fixes. items: [{"i", "heard", "proposed", "line", "around"}]. Returns {i: True} for the ones it confirms."""
    lines = []
    for it in items:
        lines.append(f"{it['i']} | HEARD: {it['heard']} | PROPOSED: {it['proposed']} | LINE: {it['line']}" + (f" | AROUND: {it['around']}" if it.get("around") else ""))
    data = ask(meter, "verify", "verify_edits", "CANDIDATES:\n" + "\n".join(lines), settings.verify_edits_model, settings.verify_edits_effort, "v")
    return {v["i"]: True for v in data["v"] if isinstance(v, dict) and isinstance(v.get("i"), int) and not isinstance(v["i"], bool) and v.get("ok") is True}
