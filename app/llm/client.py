"""Chat-completions client returning parsed JSON, for Groq (default) and any other OpenAI-compatible provider named by a prefix: "nvidia:<model>" or "openrouter:<model>"."""
import json
import time

import httpx

from app.asr.http import post_with_retry
from app.config.settings import settings
from app.utils.errors import PipelineError



PROVIDERS = {"groq": ("llm_base_url", "groq_api_key", "GROQ_API_KEY"), "nvidia": ("nvidia_base_url", "nvidia_api_key", "NVIDIA_API_KEY"),
             "openrouter": ("openrouter_base_url", "openrouter_api_key", "OPENROUTER_API_KEY")}


def split_model(spec: str) -> tuple[str, str]:
    """"nvidia:mistralai/mistral-large-2-instruct" -> ("nvidia", "mistralai/mistral-large-2-instruct"); no prefix means Groq. Only the FIRST colon counts, so
    "openrouter:qwen/qwen3-235b-a22b:free" keeps its ":free"."""
    head, _, rest = spec.partition(":")
    return (head, rest) if head in PROVIDERS and rest else ("groq", spec)


def _parse_json(text: str) -> dict:
    """The answer as JSON. Providers without a JSON mode may wrap it in a code fence or add words around it."""
    try:
        return json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(text[a : b + 1])


def chat_json(system: str, user: str, model: str | None = None, reasoning_effort: str = "low") -> dict:
    return chat_json_ex(system, user, model, reasoning_effort)[0]


def chat_json_ex(system: str, user: str, model: str | None = None, reasoning_effort: str = "low") -> tuple[dict, dict]:
    """Like chat_json, plus call metadata for tracing: {model, secs, retries, usage, effort_fallback}."""
    spec = model or settings.llm_model
    provider, model = split_model(spec)
    base_attr, key_attr, key_env = PROVIDERS[provider]
    key, base = getattr(settings, key_attr), getattr(settings, base_attr)
    if not key:
        raise PipelineError("API_KEY_MISSING", f"{key_env} is not set, but '{spec}' needs it. Add it to the .env file or choose a Groq model.")
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if provider == "groq":
        body["response_format"] = {"type": "json_object"}  # other providers: the prompts already say "JSON only", and not every model supports a JSON mode
    if provider == "groq" and model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = reasoning_effort  # low by default: narrow extraction task, hidden reasoning tokens cost money
    stats: dict = {}
    t0 = time.perf_counter()

    def call(b):
        return post_with_retry(
            base.rstrip("/") + "/chat/completions",
            provider="LLM",
            headers={"Authorization": f"Bearer {key}"},
            json=b,
            stats=stats,
            retries=settings.llm_retries,
            timeout=httpx.Timeout(settings.llm_read_timeout_seconds if provider == "groq" else settings.llm_slow_read_timeout_seconds, connect=20),
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
        data = _parse_json(payload["choices"][0]["message"]["content"])
        usage = payload.get("usage") or {}
        meta = {"model": spec, "secs": round(time.perf_counter() - t0, 3), "retries": stats.get("retries", 0),
                "rate_limited": stats.get("rate_limited", 0), "effort_fallback": stats.get("effort_fallback", False),
                "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
        return data, meta
    except (KeyError, IndexError, ValueError):
        raise PipelineError("LLM_BAD_RESPONSE", "The language model returned an unreadable answer.") from None
