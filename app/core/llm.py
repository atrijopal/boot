"""Every model call goes through here: prompt files, cache, token metering, a hard token budget and a hard deadline."""
import hashlib
import threading
import time

from app.config.settings import ROOT, settings
from app.core.schemas import Call, Usage
from app.llm.client import chat_json_ex
from app.utils import cache
from app.utils.errors import PipelineError

PROMPTS = ROOT / "prompts"
FAILOVER_CODES = ("API_RATE_LIMITED", "API_UNAVAILABLE")  # a model that is out of quota or down hands the call to FALLBACK_MODEL
EFFORT_TOKENS = {"low": 700, "medium": 1500, "high": 3000}  # expected answer size, used only for the budget check


def prompt_text(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def est_tokens(*texts: str) -> int:
    return int(sum(len(t) for t in texts) / 3.6)


def _call_model(system: str, user: str, model: str, effort: str):
    return chat_json_ex(system, user, model, effort)


def usage_of(calls: list[Call]) -> Usage:
    u = Usage(calls=len(calls), cache_hits=sum(c.cache_hit for c in calls))
    u.prompt_tokens = sum(c.prompt_tokens or 0 for c in calls)
    u.completion_tokens = sum(c.completion_tokens or 0 for c in calls)
    u.total_tokens = u.prompt_tokens + u.completion_tokens
    return u


class Meter:
    """Counts every token and call of one run and enforces the run's budget and deadline."""

    def __init__(self, max_tokens: int | None = None, timeout: int | None = None, max_calls: int | None = None):
        self.max_calls = max_calls  # hard limit on real API calls (answers reused from cache are free)
        self.max_tokens = max_tokens if max_tokens is not None else settings.max_run_tokens
        self.deadline = time.monotonic() + (timeout if timeout is not None else settings.run_timeout_seconds)
        self.calls: list[Call] = []
        self.lock = threading.Lock()

    def spent(self) -> int:
        return sum((c.prompt_tokens or 0) + (c.completion_tokens or 0) for c in self.calls)

    def api_calls(self) -> int:
        """Calls that got an answer from the API. Not cached answers, not steps skipped locally, and not failed attempts: a call the provider refused (rate limit, bad JSON) spent no
        tokens, so retrying it does not use up the cap."""
        return sum(1 for c in self.calls if not c.cache_hit and c.error is None)

    def usage(self) -> Usage:
        return usage_of(self.calls)

    def add(self, call: Call) -> None:
        with self.lock:
            call.n = len(self.calls) + 1
            self.calls.append(call)


def ask(meter: Meter, stage: str, prompt: str, user: str, model: str, effort: str, expect: str, window: str = "") -> dict:
    """One model call. `expect` is the list key the answer must contain."""
    system = prompt_text(prompt)
    base = dict(n=0, stage=stage, model=model, effort=effort, window=window, input=user)
    key = "lite_" + hashlib.sha256(f"{settings.llm_base_url}|{model}|{effort}|{system}|{user}".encode()).hexdigest()[:40]
    hit = cache.load_key(key)
    if hit is not None and isinstance(hit.get(expect), list):
        meter.add(Call(**base, cache_hit=True, output=hit))
        return hit
    if time.monotonic() > meter.deadline:
        err = PipelineError("RUN_TIMEOUT", f"The run exceeded its {settings.run_timeout_seconds}s time limit; this step was skipped.")
        meter.add(Call(**base, error=f"{err.code}: {err.message}"))
        raise err
    if meter.max_calls is not None and meter.api_calls() >= meter.max_calls:
        err = PipelineError("CALL_LIMIT", f"This phase is limited to {meter.max_calls} model calls and has used them; this step was skipped.")
        meter.add(Call(**base, error=f"{err.code}: {err.message}"))
        raise err
    need = est_tokens(system, user) + EFFORT_TOKENS.get(effort, 1500)
    if meter.spent() + need > meter.max_tokens:
        err = PipelineError("TOKEN_BUDGET", f"This call would take the run past its {meter.max_tokens}-token limit (spent {meter.spent()}, needs about {need}). Raise MAX_RUN_TOKENS to allow it.")
        meter.add(Call(**base, error=f"{err.code}: {err.message}"))
        raise err
    used, failed_over = model, None

    def attempt(m: str):
        data, info = _call_model(system, user, m, effort)
        if not isinstance(data, dict) or not isinstance(data.get(expect), list):
            raise PipelineError("BAD_RESPONSE", f"The model's answer for '{prompt}' did not contain a '{expect}' list.")
        return data, info

    try:
        try:
            data, info = attempt(model)
        except PipelineError as e:
            fb = settings.fallback_model
            if not fb or fb == model or e.code not in FAILOVER_CODES:
                raise
            meter.add(Call(**base, error=f"{e.code}: {e.message}"[:300]))  # the refused attempt stays visible, then the other provider takes over
            used, failed_over = fb, model
            data, info = attempt(fb)
    except PipelineError as e:
        meter.add(Call(**{**base, "model": used}, error=f"{e.code}: {e.message}"[:300]))
        raise
    cache.store_key(key, data)
    u = info.get("usage") or {}
    meter.add(Call(**{**base, "model": used}, fallback_for=failed_over, output=data, effort_fallback=bool(info.get("effort_fallback")), secs=info.get("secs", 0.0),
                   prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens")))
    return data
