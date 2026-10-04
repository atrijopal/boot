import json

import pytest

import app.llm.client as client
from app.utils.errors import PipelineError


class R:
    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


def test_empty_generation_is_retried_once_at_low_effort(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(kw["json"].get("reasoning_effort"))
        if len(calls) == 1:
            raise PipelineError("API_ERROR", 'LLM returned an error (HTTP 400): {"code":"json_validate_failed"}')
        return R(json.dumps({"ok": 1}))

    monkeypatch.setattr(client, "post_with_retry", fake)
    assert client.chat_json("s", "u", model="openai/gpt-oss-20b", reasoning_effort="high") == {"ok": 1}
    assert calls == ["high", "low"]


def test_other_errors_are_not_retried(monkeypatch):
    def fake(url, **kw):
        raise PipelineError("API_KEY_INVALID", "bad key")

    monkeypatch.setattr(client, "post_with_retry", fake)
    with pytest.raises(PipelineError) as e:
        client.chat_json("s", "u", model="openai/gpt-oss-20b", reasoning_effort="high")
    assert e.value.code == "API_KEY_INVALID"


def test_unreadable_answer_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(client, "post_with_retry", lambda url, **kw: R("not json"))
    with pytest.raises(PipelineError) as e:
        client.chat_json("s", "u", model="openai/gpt-oss-20b")
    assert e.value.code == "LLM_BAD_RESPONSE"
