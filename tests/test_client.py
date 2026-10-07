"""Which provider, key and URL a model name routes to, and how answers without a JSON mode are read."""
import json

import pytest

import app.llm.client as client
from app.config.settings import settings
from app.utils.errors import PipelineError


class Resp:
    def __init__(self, content):
        self.content = content

    def json(self):
        return {"choices": [{"message": {"content": self.content}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw["headers"], kw["json"]))
        return Resp(fake.content)

    fake.content = '{"ok": true}'
    monkeypatch.setattr(client, "post_with_retry", fake)
    monkeypatch.setattr(settings, "groq_api_key", "g-key")
    monkeypatch.setattr(settings, "nvidia_api_key", "n-key")
    monkeypatch.setattr(settings, "openrouter_api_key", "o-key")
    return calls, fake


def test_model_names_route_to_their_provider():
    assert client.split_model("openai/gpt-oss-120b") == ("groq", "openai/gpt-oss-120b")
    assert client.split_model("nvidia:mistralai/mistral-large-2-instruct") == ("nvidia", "mistralai/mistral-large-2-instruct")
    assert client.split_model("openrouter:qwen/qwen3-235b-a22b:free") == ("openrouter", "qwen/qwen3-235b-a22b:free")  # only the first colon counts
    assert client.split_model("something:else") == ("groq", "something:else") and client.split_model("nvidia:") == ("groq", "nvidia:")


def test_groq_calls_keep_the_json_mode_and_reasoning_effort(seen):
    calls, _ = seen
    data, meta = client.chat_json_ex("sys", "user", "openai/gpt-oss-120b", "medium")
    url, headers, body = calls[0]
    assert url == settings.llm_base_url.rstrip("/") + "/chat/completions" and headers["Authorization"] == "Bearer g-key"
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "medium" and data == {"ok": True} and meta["model"] == "openai/gpt-oss-120b"


def test_other_providers_use_their_own_url_key_and_no_groq_only_options(seen):
    calls, fake = seen
    fake.content = 'Here you go:\n```json\n{"items": []}\n```'
    data, meta = client.chat_json_ex("sys", "user", "nvidia:mistralai/mistral-large-2-instruct", "medium")
    url, headers, body = calls[-1]
    assert url == "https://integrate.api.nvidia.com/v1/chat/completions" and headers["Authorization"] == "Bearer n-key"
    assert body["model"] == "mistralai/mistral-large-2-instruct" and "response_format" not in body and "reasoning_effort" not in body
    assert data == {"items": []} and meta["model"] == "nvidia:mistralai/mistral-large-2-instruct"  # the cache and the page see the full name
    client.chat_json_ex("sys", "user", "openrouter:qwen/qwen3-235b-a22b:free", "low")
    assert calls[-1][0] == "https://openrouter.ai/api/v1/chat/completions" and calls[-1][1]["Authorization"] == "Bearer o-key" and calls[-1][2]["model"] == "qwen/qwen3-235b-a22b:free"


def test_a_missing_key_for_a_prefixed_model_says_which_key_and_what_to_do(seen, monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    with pytest.raises(PipelineError) as e:
        client.chat_json_ex("sys", "user", "nvidia:meta/llama-3.1-405b-instruct", "low")
    assert e.value.code == "API_KEY_MISSING" and "NVIDIA_API_KEY" in e.value.message and "nvidia:meta/llama-3.1-405b-instruct" in e.value.message


def test_an_answer_that_is_not_json_at_all_is_an_error_not_a_crash(seen):
    _, fake = seen
    fake.content = "I cannot help with that."
    with pytest.raises(PipelineError) as e:
        client.chat_json_ex("sys", "user", "nvidia:x/y", "low")
    assert e.value.code == "LLM_BAD_RESPONSE"


def test_a_call_made_at_lower_effort_after_invalid_json_is_flagged(wav, monkeypatch):
    import app.core.llm as llm
    from tests.support import FakeLLM, make_transcript

    fake = FakeLLM(make_transcript())
    monkeypatch.setattr(llm, "_call_model", lambda system, user, model, effort: ({"edits": []}, {"model": model, "secs": 0.1, "effort_fallback": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
    m = llm.Meter()
    llm.ask(m, "refine", "refine", "x", "openai/gpt-oss-20b", "medium", "edits")
    assert m.calls[0].effort_fallback is True


# ---------------------------------------------------------------------------------------------- failover
def _ask(monkeypatch, behaviour, fallback="nvidia:nvidia/nemotron-3-super-120b-a12b", model="openai/gpt-oss-120b"):
    import app.core.llm as llm

    seen = []

    def fake(system, user, m, effort):
        seen.append(m)
        return behaviour(m)

    monkeypatch.setattr(llm, "_call_model", fake)
    monkeypatch.setattr(settings, "fallback_model", fallback)
    meter = llm.Meter()
    return meter, seen, lambda: llm.ask(meter, "extract", "extract", "x", model, "medium", "items")


def test_a_rate_limited_call_is_answered_by_the_fallback_model_and_both_attempts_are_recorded(monkeypatch):
    def behaviour(m):
        if m.startswith("openai/"):
            raise PipelineError("API_RATE_LIMITED", "daily token limit reached")
        return {"items": []}, {"secs": 1.0, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    meter, seen, run = _ask(monkeypatch, behaviour)
    assert run() == {"items": []} and seen == ["openai/gpt-oss-120b", "nvidia:nvidia/nemotron-3-super-120b-a12b"]
    first, second = meter.calls
    assert first.error and first.model == "openai/gpt-oss-120b" and second.model == "nvidia:nvidia/nemotron-3-super-120b-a12b" and second.fallback_for == "openai/gpt-oss-120b"
    assert meter.api_calls() == 1 and meter.usage().total_tokens == 15  # the refused attempt spent nothing and does not count against the cap


def test_other_errors_and_a_missing_fallback_do_not_fail_over(monkeypatch):
    def bad_json(m):
        raise PipelineError("API_ERROR", "HTTP 400 invalid request")

    meter, seen, run = _ask(monkeypatch, bad_json)
    with pytest.raises(PipelineError):
        run()
    assert seen == ["openai/gpt-oss-120b"]  # a bad request would be bad everywhere
    meter, seen, run = _ask(monkeypatch, lambda m: (_ for _ in ()).throw(PipelineError("API_RATE_LIMITED", "limit")), fallback="")
    with pytest.raises(PipelineError):
        run()
    assert seen == ["openai/gpt-oss-120b"]


def test_if_the_fallback_fails_too_the_error_names_the_fallback_model(monkeypatch):
    def both_down(m):
        raise PipelineError("API_UNAVAILABLE", f"{m} is down")

    meter, seen, run = _ask(monkeypatch, both_down)
    with pytest.raises(PipelineError):
        run()
    assert seen == ["openai/gpt-oss-120b", "nvidia:nvidia/nemotron-3-super-120b-a12b"] and "nemotron" in meter.calls[-1].model and len(meter.calls) == 2
