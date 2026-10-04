import time

from fastapi.testclient import TestClient

from app.api.main import app
from tests.test_refinement_pipeline import GOOD, NUMBER, UNSUPPORTED, TEXTS, job, mock_llm  # noqa: F401  (fixtures)


def wait(client, job_id, key="refine"):
    for _ in range(100):
        j = client.get(f"/jobs/{job_id}").json()
        if j.get(key, {}).get("stage") in ("done", "failed"):
            return j[key]
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_refine_decide_download_flow(job, monkeypatch):
    mock_llm(monkeypatch, [GOOD, UNSUPPORTED, NUMBER])
    c = TestClient(app)
    assert c.post(f"/jobs/{job}/refine").status_code == 200
    r = wait(c, job)
    assert r["stage"] == "done"
    res = r["result"]
    ids = {e["find"]: e["edit_id"] for e in res["edits"]}
    assert res["refined_transcript"]["segments"][0]["text"].startswith("We track PSNR")

    ok = c.post(f"/jobs/{job}/edits/{ids['kube control']}", json={"decision": "accept"})
    assert ok.status_code == 200 and "kubectl" in ok.json()["refined_transcript"]["segments"][2]["text"]
    blocked = c.post(f"/jobs/{job}/edits/{ids['81.73']}", json={"decision": "accept"})
    assert blocked.status_code == 409 and "blocked" in blocked.json()["detail"]
    assert c.post(f"/jobs/{job}/edits/{ids['kube control']}", json={"decision": "maybe"}).status_code == 400

    txt = c.get(f"/jobs/{job}/refined?format=txt").text
    assert "PSNR" in txt and "kubectl" in txt and "pee ess en are" not in txt
    assert "edit_id" in c.get(f"/jobs/{job}/refined?format=csv").text
    assert c.get(f"/jobs/{job}/refined?format=exe").status_code == 400
    assert c.get(f"/jobs/{job}/refinement").json()["invariants"]["numbers_unchanged"] is True


def test_refine_unknown_job_and_failure_are_clean(job, monkeypatch):
    c = TestClient(app)
    assert c.post("/jobs/nope/refine").status_code == 404
    from app.utils.errors import PipelineError
    mock_llm(monkeypatch, PipelineError("API_RATE_LIMITED", "rate limited"))
    c.post(f"/jobs/{job}/refine")
    r = wait(c, job)
    assert r["stage"] == "failed" and r["error"]["code"] == "REFINE_UNAVAILABLE" and "Traceback" not in str(r)
