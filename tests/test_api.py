import time

from fastapi.testclient import TestClient

import app.api as api
from tests.support import install


def wait(c, job):
    for _ in range(200):
        j = c.get(f"/jobs/{job}").json()
        if j["stage"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_upload_poll_results_downloads_and_reopen_after_restart(wav, monkeypatch):
    install(monkeypatch)
    c = TestClient(api.app)
    with open(wav, "rb") as f:
        job = c.post("/process", files={"file": ("meeting.wav", f)}, data={"provider": "", "through": "3"}).json()["job_id"]
    j = wait(c, job)
    assert j["stage"] == "done" and j["result"]["usage"]["calls"] == 3 and len(j["result"]["records"]) >= 5
    assert any(x["job_id"] == job and x["file"] == "meeting.wav" for x in c.get("/jobs").json())
    md = c.get(f"/jobs/{job}/download?format=md")
    assert md.status_code == 200 and "# Meeting minutes" in md.text
    assert "act_001" in c.get(f"/jobs/{job}/download?format=csv").text
    assert c.get(f"/jobs/{job}/download?format=calls").json()[0]["stage"] == "refine"
    page = c.get(f"/jobs/{job}/report")
    assert page.status_code == 200 and "Meeting report" in page.text and "meeting.wav" in page.text
    assert "Meeting report" in c.get(f"/jobs/{job}/download?format=html").text
    assert c.get(f"/jobs/{job}/download?format=exe").status_code == 400
    api.jobs.clear()  # a server restart: everything is rebuilt from disk
    assert c.get(f"/jobs/{job}").json()["result"]["job_id"] == job
    assert c.get("/jobs/nope").status_code == 404


def test_bad_uploads_give_clear_errors_without_stack_traces(monkeypatch):
    fake = install(monkeypatch)
    c = TestClient(api.app)
    for name, data, code in [("empty.wav", b"", "EMPTY_FILE"), ("x.txt", b"hello", "UNSUPPORTED_FORMAT"), ("fake.mp3", b"not audio at all....", "UNSUPPORTED_FORMAT")]:
        job = c.post("/process", files={"file": (name, data)}).json()["job_id"]
        j = wait(c, job)
        assert j["stage"] == "failed" and j["error"]["code"] == code and "Traceback" not in str(j)
    assert fake.calls == []
    assert c.post("/process", files={"file": ("a.wav", b"x")}, data={"provider": "nope"}).status_code == 400


def test_an_unreachable_model_fails_the_job_with_a_message(wav, monkeypatch):
    fake = install(monkeypatch)
    fake.fail = {"extract"}
    c = TestClient(api.app)
    with open(wav, "rb") as f:
        job = c.post("/process", files={"file": ("m.wav", f)}, data={"through": "3"}).json()["job_id"]
    j = wait(c, job)
    assert j["stage"] == "failed" and j["error"]["code"] == "ANALYSIS_FAILED"


def test_page_and_static_files_are_served():
    c = TestClient(api.app)
    assert "Meeting Record" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200 and c.get("/static/app.css").status_code == 200
    assert c.get("/health").json()["status"] == "ok"


def test_phases_run_one_at_a_time_and_each_saves_its_own_result(wav, monkeypatch):
    fake = install(monkeypatch)
    c = TestClient(api.app)
    with open(wav, "rb") as f:
        job = c.post("/process", files={"file": ("meeting.wav", f)}).json()["job_id"]
    j = wait(c, job)
    assert j["stage"] == "done" and "phase1" in j and "phase2" not in j and "result" not in j and fake.calls == []  # phase 1 costs no model call
    assert j["phase1"]["facts"]["numbers"] and j["phase1"]["transcript"]["segments"]
    assert c.post(f"/jobs/{job}/phase/1").status_code == 400
    assert c.post("/jobs/nope/phase/2").status_code == 404
    c.post(f"/jobs/{job}/phase/2")
    j = wait(c, job)
    assert j["stage"] == "done" and j["phase2"]["edits"] and "result" not in j and [x[0] for x in fake.calls] == ["refine"]
    c.post(f"/jobs/{job}/phase/3")
    j = wait(c, job)
    assert j["stage"] == "done" and j["result"]["usage"]["calls"] == 3 and [x[0] for x in fake.calls] == ["refine", "extract", "review"]
    assert c.get("/jobs").json()[0]["phase"] == 3
    api.jobs.clear()
    assert set(c.get(f"/jobs/{job}").json()) >= {"phase1", "phase2", "result"}
