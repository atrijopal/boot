"""OpenAI-compatible JSON chat client shared by Phases 1–3."""
import json
from urllib.parse import urlsplit

from app.asr.http import post_with_retry
from app.config.settings import settings
from app.utils.errors import PipelineError



def chat_api_key() -> str:
    if settings.llm_api_key:
        return settings.llm_api_key
    endpoint = urlsplit(settings.llm_base_url)
    if endpoint.scheme == "https" and endpoint.netloc == "api.groq.com" and settings.groq_api_key:
        return settings.groq_api_key
    raise PipelineError("API_KEY_MISSING", "Set LLM_API_KEY for your chat endpoint, or GROQ_API_KEY for the default Groq endpoint, in .env.")


def chat_json(system: str, user: str, model: str | None = None, reasoning_effort: str = "low") -> dict:
    api_key = chat_api_key()
    model = model or settings.llm_model
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = reasoning_effort  # low by default: narrow extraction task, hidden reasoning tokens cost money
    def call(b):
        return post_with_retry(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            provider="LLM",
            headers={"Authorization": f"Bearer {api_key}"},
            json=b,
        )

    try:
        resp = call(body)
    except PipelineError as e:
        # Reasoning models can spend the whole budget thinking and return nothing ("json_validate_failed").
        # Retry once with minimal reasoning instead of losing the batch.
        if "json_validate_failed" not in e.message or body.get("reasoning_effort") in (None, "low"):
            raise
        resp = call({**body, "reasoning_effort": "low"})
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value
    except (KeyError, IndexError, ValueError, TypeError):
        raise PipelineError("LLM_BAD_RESPONSE", "The language model returned an unreadable answer.") from None
