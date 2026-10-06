"""One-run workflow: POST /process-audio with run_all=true chains transcription -> refinement -> minutes on the server.

The three phase functions are replaced by fakes that record the order they ran in; the phases themselves are tested elsewhere.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
from app.utils.errors import PipelineError


class _Res:
    def __init__(self, d):
        self.d = d

    def model_dump(self):
        return self.d


@pytest.fixture
def phases(monkeypatch):
    """Fake phases. `fail` names the phase that raises; `gate` holds refinement until the test releases it."""
    calls, fail, gate = [], set(), threading.Event()
    gate.set()

    def phase1(path, job_id, provider_name, status):
        calls.append("phase1")
        status("transcribing", "fake ASR")
        if "phase1" in fail:
            raise PipelineError("EMPTY_AUDIO", "The file contains no audio.")
        return _Res({"job_id": job_id})

    def refine(job_id, status):
        calls.append("refine")
        gate.wait(5)
        if "refine" in fail:
            raise PipelineError("REFINE_UNAVAILABLE", "The refinement model is unavailable.")
        return _Res({"job_id": job_id})

    def minutes(job_id, status):
        calls.append("minutes")
        status("extract_all", "Extracting")
        status("extract_all:done", "")
        if "minutes" in fail:
            raise PipelineError("MINUTES_INPUT_INVALID", "The refinement checks did not pass.")

    monkeypatch.setattr(api, "run_phase1", phase1)
    monkeypatch.setattr(api, "run_refinement", refine)
    monkeypatch.setattr(api, "run_minutes", minutes)
    monkeypatch.setattr(api, "load_minutes", lambda job_id: (object(), False))
    monkeypatch.setattr(api, "to_export", lambda m: {"status": "complete"})
    return calls, fail, gate


def _upload(c, **form):
    r = c.post("/process-audio", files={"file": ("m.wav", b"RIFF0000", "audio/wav")}, data=form)
    assert r.status_code == 200
    return r.json()["job_id"]


def _settle(c, job, keys=("refine", "minutes")):
    for _ in range(200):
        j = c.get(f"/jobs/{job}").json()
        if j["stage"] in ("done", "failed") and all(j.get(k, {}).get("stage") in (None, "done", "failed", "skipped") for k in keys):
            return j
        time.sleep(0.02)
    raise AssertionError("timeout")


def test_run_all_chains_the_three_phases_in_order(phases):
    calls, _, _ = phases
    c = TestClient(api.app)
    job = _upload(c, run_all="true")
    j = _settle(c, job)
    assert calls == ["phase1", "refine", "minutes"]
    assert j["run_all"] is True and j["stage"] == "done"
    assert j["refine"]["stage"] == "done" and j["minutes"]["stage"] == "done" and j["minutes"]["result"]["status"] == "complete"


def test_without_run_all_only_phase1_runs(phases):
    calls, _, _ = phases
    c = TestClient(api.app)
    j = _settle(c, _upload(c))
    assert calls == ["phase1"] and "refine" not in j and "minutes" not in j and j["run_all"] is False


def test_old_auto_refine_flag_still_stops_after_refinement(phases):
    calls, _, _ = phases
    c = TestClient(api.app)
    j = _settle(c, _upload(c, auto_refine="true"))
    assert calls == ["phase1", "refine"] and j["refine"]["stage"] == "done" and "minutes" not in j


def test_failed_transcription_skips_later_steps_with_a_reason(phases):
    calls, fail, _ = phases
    fail.add("phase1")
    c = TestClient(api.app)
    j = _settle(c, _upload(c, run_all="true"))
    assert calls == ["phase1"]
    assert j["stage"] == "failed" and j["error"]["code"] == "EMPTY_AUDIO"
    assert j["refine"]["stage"] == "skipped" and "step 1" in j["refine"]["message"]
    assert j["minutes"]["stage"] == "skipped"


def test_failed_refinement_skips_minutes_and_both_can_be_retried(phases):
    calls, fail, _ = phases
    fail.add("refine")
    c = TestClient(api.app)
    job = _upload(c, run_all="true")
    j = _settle(c, job)
    assert calls == ["phase1", "refine"]
    assert j["refine"]["stage"] == "failed" and j["minutes"]["stage"] == "skipped" and "step 2" in j["minutes"]["message"]
    assert "Traceback" not in str(j)
    fail.clear()  # a skipped or failed step must not block a manual retry
    assert c.post(f"/jobs/{job}/refine").status_code == 200
    assert _settle(c, job, ("refine",))["refine"]["stage"] == "done"


def test_failed_minutes_is_reported(phases):
    _, fail, _ = phases
    fail.add("minutes")
    c = TestClient(api.app)
    j = _settle(c, _upload(c, run_all="true"))
    assert j["refine"]["stage"] == "done" and j["minutes"]["stage"] == "failed" and j["minutes"]["error"]["code"] == "MINUTES_INPUT_INVALID"


def test_steps_queued_by_the_chain_cannot_be_started_twice(phases):
    calls, _, gate = phases
    gate.clear()  # hold refinement so steps 2 and 3 stay in progress / queued
    c = TestClient(api.app)
    job = _upload(c, run_all="true")
    for _ in range(200):
        if c.get(f"/jobs/{job}").json()["stage"] == "done":
            break
        time.sleep(0.02)
    j = c.get(f"/jobs/{job}").json()
    assert j["minutes"]["stage"] == "queued"
    r = c.post(f"/jobs/{job}/refine")
    assert r.status_code == 409 and "already" in r.json()["detail"]
    gate.set()
    _settle(c, job)
    assert calls == ["phase1", "refine", "minutes"]  # the rejected request did not start a second refinement
