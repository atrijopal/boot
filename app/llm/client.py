"""Minimal Groq chat-completions client returning parsed JSON. Used only for optional Phase-1 labelling."""
import json
import time

import httpx

from app.asr.http import post_with_retry
from app.config.settings import settings
from app.utils.errors import PipelineError



def chat_json(system: str, user: str, model: str | None = None, reasoning_effort: str = "low") -> dict:
    return chat_json_ex(system, user, model, reasoning_effort)[0]


def chat_json_ex(system: str, user: str, model: str | None = None, reasoning_effort: str = "low") -> tuple[dict, dict]:
    """Like chat_json, plus call metadata for tracing: {model, secs, retries, usage, effort_fallback}."""
    if not settings.groq_api_key:
        raise PipelineError("API_KEY_MISSING", "GROQ_API_KEY is not set. Add it to the .env file.")
    model = model or settings.llm_model
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = reasoning_effort  # low by default: narrow extraction task, hidden reasoning tokens cost money
    stats: dict = {}
    t0 = time.perf_counter()

    def call(b):
        return post_with_retry(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            provider="LLM",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json=b,
            stats=stats,
            retries=3,
            timeout=httpx.Timeout(90, connect=20),
            max_wait=45,
        )

    try:
        resp = call(body)
    except PipelineError as e:
        # Reasoning models can spend the whole budget thinking and return nothing ("json_validate_failed").
        # Retry once with minimal reasoning instead of losing the batch.
        if "json_validate_failed" not in e.message or body.get("reasoning_effort") in (None, "low"):
            raise
        stats["effort_fallback"] = True
        resp = call({**body, "reasoning_effort": "low"})
    try:
        payload = resp.json()
        data = json.loads(payload["choices"][0]["message"]["content"])
        usage = payload.get("usage") or {}
        meta = {"model": model, "secs": round(time.perf_counter() - t0, 3), "retries": stats.get("retries", 0),
                "rate_limited": stats.get("rate_limited", 0), "effort_fallback": stats.get("effort_fallback", False),
                "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
        return data, meta
    except (KeyError, IndexError, ValueError):
        raise PipelineError("LLM_BAD_RESPONSE", "The language model returned an unreadable answer.") from None
