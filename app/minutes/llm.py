"""One place for every Phase 3 model call: prompt files, cache, global concurrency limit, tracing.

Opt-in LangSmith: set LANGSMITH_TRACING=true and LANGSMITH_API_KEY. This sends prompts and transcripts to LangSmith's
cloud, so it is OFF by default. The built-in trace (trace.jsonl) never leaves the machine.
"""
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field

from app.config.settings import ROOT, settings
from app.llm.client import chat_json_ex
from app.minutes.stagelog import StageLog
from app.minutes.trace import Tracer
from app.schemas.facts import ProtectedFacts
from app.schemas.transcript import Transcript
from app.utils import cache
from app.utils.errors import PipelineError

PROMPTS_DIR = ROOT / "prompts" / "minutes"
PROMPT_NAMES = ("extract_all", "extract_agreements", "extract_actions", "extract_questions_risks", "extract_facts", "verify_items",
                "reconcile", "topics", "topic_summary", "executive", "verify_claims")


def prompt_text(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def prompt_hashes() -> dict[str, str]:
    return {n: hashlib.sha256((PROMPTS_DIR / f"{n}.md").read_bytes()).hexdigest()[:12] for n in PROMPT_NAMES}


def _call_model(system: str, user: str, model: str, effort: str):
    return chat_json_ex(system, user, model, effort)


if os.getenv("LANGSMITH_TRACING", "").lower() in ("1", "true", "yes"):  # opt-in, off by default
    try:
        from langsmith import traceable

        _call_model = traceable(run_type="llm", name="groq_chat")(_call_model)
    except Exception:  # tracing must never break a run
        pass


@dataclass
class Ctx:
    """Everything a node needs; shared read-only inputs plus the tracer and the global call limiter."""
    job_id: str
    tracer: Tracer
    transcript: Transcript | None = None  # the refined transcript this run works on
    facts: ProtectedFacts | None = None  # Phase 1 facts (numbers, names, terms)
    participants: str = ""  # "Alima (SPEAKER_01), Rose (SPEAKER_00)" shown to every extraction prompt
    deadline: float = field(default_factory=lambda: time.monotonic() + settings.minutes_max_seconds)  # no run may go on forever
    log: StageLog = field(default_factory=StageLog)  # every call + every keep/drop decision, for the UI
    sem: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(max(1, settings.minutes_concurrency)))


def ask(ctx: Ctx, node: str, prompt: str, user: str, model: str, effort: str, expect: str, ref: str = "") -> dict:
    """Call the model with prompts/minutes/<prompt>.md as the system message. `expect` = the list key the answer must contain."""
    system = prompt_text(prompt)
    key = "min_" + hashlib.sha256(f"{settings.llm_base_url}|{model}|{effort}|{system}|{user}".encode()).hexdigest()[:40]
    hit = cache.load_key(key)
    base = {"node": node, "ref": ref, "prompt": prompt, "model": model, "effort": effort, "input": user}
    if hit is not None and isinstance(hit.get(expect), list):
        ctx.tracer.llm(node, prompt, model, None, True, len(system) + len(user), len(str(hit)))
        ctx.log.call({**base, "cache_hit": True, "output": hit, "secs": 0.0, "error": None})
        return hit
    if time.monotonic() > ctx.deadline:
        err = PipelineError("MINUTES_TIMEOUT", f"The minutes run exceeded its {settings.minutes_max_seconds}s time limit; this step was skipped.")
        ctx.tracer.llm(node, prompt, model, None, False, len(system) + len(user), 0, error=err.code)
        ctx.log.call({**base, "cache_hit": False, "output": None, "secs": 0.0, "error": f"{err.code}: {err.message}"})
        raise err
    try:
        with ctx.sem:
            data, meta = _call_model(system, user, model, effort)
        if not isinstance(data, dict) or not isinstance(data.get(expect), list):
            raise PipelineError("MINUTES_BAD_RESPONSE", f"The model's answer for '{prompt}' did not contain a '{expect}' list.")
    except PipelineError as e:
        ctx.tracer.llm(node, prompt, model, None, False, len(system) + len(user), 0, error=e.code)
        ctx.log.call({**base, "cache_hit": False, "output": None, "secs": 0.0, "error": f"{e.code}: {e.message}"[:200]})
        raise
    cache.store_key(key, data)
    ctx.tracer.llm(node, prompt, model, meta, False, len(system) + len(user), len(str(data)))
    usage = meta.get("usage") or {}
    ctx.log.call({**base, "cache_hit": False, "output": data, "secs": meta.get("secs", 0.0), "retries": meta.get("retries", 0),
                  "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"), "error": None})
    return data
