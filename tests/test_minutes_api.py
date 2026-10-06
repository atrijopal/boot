import time

from fastapi.testclient import TestClient

import app.minutes.llm as mllm
from app.api.main import app
from tests.minutes_support import FakeLLM, make_job


def wait(c, job, key="minutes"):
    for _ in range(200):
        j = c.get(f"/jobs/{job}").json()
        if j.get(key, {}).get("stage") in ("done", "failed"):
            return j[key]
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_minutes_flow_and_downloads(monkeypatch):
    monkeypatch.setattr(mllm, "_call_model", FakeLLM())
    job = make_job("api1")
    c = TestClient(app)
    assert c.post(f"/jobs/{job}/minutes").status_code == 200
    r = wait(c, job)
    assert r["stage"] == "done"
    res = r["result"]
    assert res["status"] == "complete" and res["stale"] is False and len(res["registers"]["decisions"]) == 1 and len(res["registers"]["action_items"]) == 2
    got = c.get(f"/jobs/{job}/minutes").json()
    assert got["meta"]["run_id"] == res["meta"]["run_id"]
    md = c.get(f"/jobs/{job}/minutes/download?format=md")
    assert md.status_code == 200 and "# Meeting minutes" in md.text and "Owner: Ian" in md.text and "Owner: Jessie" in md.text
    assert "act_001" in c.get(f"/jobs/{job}/minutes/download?format=csv").text
    assert c.get(f"/jobs/{job}/minutes/download?format=json").json()["schema_version"] == "minutes/1"
    assert '"kind": "llm"' in c.get(f"/jobs/{job}/minutes/download?format=trace").text
    assert c.get(f"/jobs/{job}/minutes/download?format=exe").status_code == 400


def test_minutes_need_phase2_and_unknown_jobs_are_clean(monkeypatch):
    monkeypatch.setattr(mllm, "_call_model", FakeLLM())
    c = TestClient(app)
    assert c.post("/jobs/nope/minutes").status_code == 404
    assert c.get("/jobs/nope/minutes").status_code == 404
    job = make_job("api2")
    (__import__("pathlib").Path(__import__("app.config.settings", fromlist=["settings"]).settings.outputs_dir) / job / "refinement.json").unlink()
    r = c.post(f"/jobs/{job}/minutes")
    assert r.status_code == 409 and "Phase 2" in r.json()["detail"]


def test_total_llm_outage_is_reported_without_a_stack_trace(monkeypatch):
    fake = FakeLLM()
    fake.fail = set(fake.by_text.values())
    monkeypatch.setattr(mllm, "_call_model", fake)
    job = make_job("api3")
    c = TestClient(app)
    c.post(f"/jobs/{job}/minutes")
    r = wait(c, job)
    assert r["stage"] == "done"  # the run completes as partial: nothing is invented
    assert r["result"]["status"] == "partial" and r["result"]["registers"]["decisions"] == []
    assert "Traceback" not in str(r)


def test_job_list_rebuild_after_restart_stages_and_static_files(monkeypatch):
    import app.api.main as api
    monkeypatch.setattr(mllm, "_call_model", FakeLLM())
    job = make_job("api9")
    c = TestClient(app)
    c.post(f"/jobs/{job}/minutes")
    r = wait(c, job)
    assert r["stage"] == "done"
    assert r["result"]["status"] == "complete"
    listing = c.get("/jobs").json()
    mine = next(j for j in listing if j["job_id"] == job)
    assert mine["file"] == "a.wav" and mine["has_refinement"] and mine["has_minutes"]
    api.jobs.clear()  # simulate a server restart: everything must be rebuilt from disk
    j = c.get(f"/jobs/{job}").json()
    assert j["result"]["job_id"] == job and j["refine"]["result"]["job_id"] == job and j["minutes"]["result"]["meta"]["job_id"] == job
    assert j["minutes"]["result"]["stale"] is False
    stages = c.get(f"/jobs/{job}/minutes/stages").json()
    assert stages["calls"] and "extraction" in stages["outcomes"]
    assert c.get(f"/jobs/{job}/minutes/download?format=stages").status_code == 200
    assert c.get("/static/app.js").status_code in (200, 404)  # served from web/static once the front-end exists
    assert c.get("/jobs/nope").status_code == 404


def test_progress_lists_finished_and_running_steps(monkeypatch):
    monkeypatch.setattr(mllm, "_call_model", FakeLLM())
    job = make_job("api10")
    c = TestClient(app)
    seen = []
    import app.api.main as api
    orig = api._set

    def spy(job_id, **kw):
        if "minutes" in kw:
            seen.append(kw["minutes"])
        return orig(job_id, **kw)

    monkeypatch.setattr(api, "_set", spy)
    c.post(f"/jobs/{job}/minutes")
    wait(c, job)
    running = [s for s in seen if s["stage"] == "running"]
    assert running and "done" in running[-1] and "verify" in running[-1]["done"]
    assert {"chunk", "topics", "executive"} <= set(running[-1]["done"])
