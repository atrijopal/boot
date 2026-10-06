"""Token pacing, patient retries, second try for failed chunks, and the combined extraction mode."""
import threading
import time

import pytest

import app.minutes.llm as mllm
import app.minutes.pipeline as mp
from app.config.settings import settings
from app.minutes.llm import Ctx, ask
from app.minutes.trace import Tracer
from app.utils.errors import PipelineError
from tests.minutes_support import FakeLLM, make_job
from tests.test_minutes_pipeline import llm_combined  # noqa: F401  (fixture)


# ---------------------------------------------------------------- ask(): patience and caching
def make_ctx():
    return Ctx(job_id="x", tracer=Tracer())


def scripted(monkeypatch, outcomes):
    calls = []

    def fake(system, user, model, effort):
        calls.append(1)
        out = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return {"items": []}, {"model": model, "secs": 0.01, "retries": 0, "rate_limited": 0, "usage": {"total_tokens": 50}}

    monkeypatch.setattr(mllm, "_call_model", fake)
    return calls


def test_a_failed_call_is_reported_once_and_never_retried_in_a_loop(monkeypatch):
    calls = scripted(monkeypatch, [PipelineError("API_RATE_LIMITED", "slow")])
    ctx = make_ctx()
    with pytest.raises(PipelineError) as e:
        ask(ctx, "n", "extract_all", "u", "m", "low", "items")
    assert e.value.code == "API_RATE_LIMITED" and len(calls) == 1 and ctx.tracer.summary()["failed_calls"] == 1


def test_a_run_past_its_deadline_skips_further_calls_with_a_clear_error(monkeypatch):
    calls = scripted(monkeypatch, ["ok"])
    ctx = make_ctx()
    ctx.deadline = time.monotonic() - 1
    with pytest.raises(PipelineError) as e:
        ask(ctx, "n", "extract_all", "u", "m", "low", "items")
    assert e.value.code == "MINUTES_TIMEOUT" and calls == []
    assert ctx.log.export()["calls"][0]["error"].startswith("MINUTES_TIMEOUT")


def test_a_run_that_hits_its_limit_ends_partial_not_stuck(monkeypatch):
    from tests.minutes_support import make_job
    fake = FakeLLM()
    monkeypatch.setattr(mllm, "_call_model", fake)
    monkeypatch.setattr(settings, "minutes_max_seconds", -1)  # the limit has already passed when the run starts
    m = mp.run_minutes(make_job("dl1"))
    assert m.status == "partial" and m.items == [] and fake.calls == [] and any("not scanned" in w or "time limit" in w for w in m.warnings + [str(m.stats)])


def test_llm_http_calls_use_short_timeouts_and_few_retries(monkeypatch):
    import httpx
    import app.llm.client as client
    seen = {}

    def fake_post(url, **kw):
        seen.update(kw)

        class R:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "{}"}}], "usage": {}}
        return R()

    monkeypatch.setattr(client, "post_with_retry", fake_post)
    client.chat_json("s", "u", model="openai/gpt-oss-20b")
    assert seen["retries"] == 3 and seen["max_wait"] <= 60 and isinstance(seen["timeout"], httpx.Timeout) and seen["timeout"].read <= 120


def test_bad_shape_is_rejected(monkeypatch):
    monkeypatch.setattr(mllm, "_call_model", lambda *a: ({"wrong": []}, {"usage": {}}))
    with pytest.raises(PipelineError) as e:
        ask(make_ctx(), "n", "extract_all", "u", "m", "low", "items")
    assert e.value.code == "MINUTES_BAD_RESPONSE"


# ---------------------------------------------------------------- combined mode and chunk retry
def test_combined_mode_gives_the_same_records_with_one_extraction_call_per_chunk(llm_combined):
    m = mp.run_minutes(make_job("c1"))
    assert m.status == "complete"
    assert llm_combined.count("extract_all") == 1 and sum(llm_combined.count(n) for n in ("extract_agreements", "extract_actions", "extract_questions_risks", "extract_facts")) == 0
    assert [i.item_id for i in m.items if i.lens == "decision"] == ["dec_001"] and len([i for i in m.items if i.lens == "action"]) == 2
    assert len([i for i in m.items if i.lens == "fact"]) == 2 and len([i for i in m.items if i.lens == "risk"]) == 1
    assert "extract_all" in m.trace_summary["node_seconds"]


def test_combined_mode_scans_every_chunk(llm_combined, monkeypatch):
    monkeypatch.setattr(settings, "minutes_chunk_segments", 4)
    monkeypatch.setattr(settings, "minutes_context_segments", 2)
    m = mp.run_minutes(make_job("c2"))
    assert m.meta.chunks == 3 and llm_combined.count("extract_all") == 3


def test_a_failed_chunk_gets_a_second_try_and_the_run_completes(monkeypatch):
    fake = FakeLLM()
    state = {"failed_once": False}
    orig = fake.r_extract_all

    def flaky(user):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise PipelineError("API_ERROR", "transient")
        return orig(user)

    fake.r_extract_all = flaky
    monkeypatch.setattr(mllm, "_call_model", fake)
    monkeypatch.setattr(settings, "minutes_extract_mode", "combined")
    m = mp.run_minutes(make_job("c3"))
    assert m.status == "complete" and m.stats["failed_chunks"] == 0 and any(i.lens == "decision" for i in m.items)
    assert fake.count("extract_all") == 2


def test_a_chunk_that_fails_twice_is_reported_not_hidden(monkeypatch):
    fake = FakeLLM()
    fake.fail = {"extract_all"}
    monkeypatch.setattr(mllm, "_call_model", fake)
    monkeypatch.setattr(settings, "minutes_extract_mode", "combined")
    m = mp.run_minutes(make_job("c4"))
    assert m.status == "partial" and m.stats["failed_chunks"] == 1 and any("not scanned" in w for w in m.warnings) and m.items == []
