import json
import time

from fastapi.testclient import TestClient

from app.api.main import app
from app.summarization.api import active, pool
from app.summarization.pipeline import run_phase3


def test_summary_routes_downloads_and_staleness(phase3_job):
    job_id, root, fake = phase3_job
    client = TestClient(app)
    current = client.get(f"/jobs/{job_id}/summary").json()
    assert current["stage"] == "not_started"
    assert client.post(f"/jobs/{job_id}/summarize", json={"source_revision": "old"}).status_code == 409
    assert client.post(f"/jobs/{job_id}/summarize", json={"source_revision": current["source_revision"]}).status_code == 200
    for _ in range(150):
        result = client.get(f"/jobs/{job_id}/summary").json()
        if result["stage"] in ("complete", "partial", "failed"):
            break
        time.sleep(.02)
    assert result["stage"] == "complete", result
    assert client.get(f"/jobs/{job_id}/phase3/download/minutes").status_code == 200
    assert client.get(f"/jobs/{job_id}/phase3/download/secret").status_code == 400
    assert client.get(f"/jobs/{job_id}/topics").json()["membership_calibrated"] is False
    assert client.get(f"/jobs/{job_id}/graph").json()["nodes"]
    data = json.loads((root / "refinement.json").read_text())
    data["manual_names"]["S1"] = "Edited"
    (root / "refinement.json").write_text(json.dumps(data))
    assert client.get(f"/jobs/{job_id}/summary").json()["stage"] == "stale"
    assert client.get(f"/jobs/{job_id}/phase3/download/minutes").status_code == 409


def test_duplicate_running_jobs_are_rejected(phase3_job, monkeypatch):
    client = TestClient(app)
    job_id = phase3_job[0]
    monkeypatch.setattr(pool, "submit", lambda *a, **k: None)
    assert client.post(f"/jobs/{job_id}/summarize").status_code == 200
    assert client.post(f"/jobs/{job_id}/summarize").status_code == 409
    active.clear()


def test_restart_reads_disk_and_unknown_job_is_clean(phase3_job):
    run_phase3(phase3_job[0])
    active.clear()
    client = TestClient(app)
    assert client.get(f"/jobs/{phase3_job[0]}/summary").json()["stage"] == "complete"
    assert client.post("/jobs/nope/summarize").status_code == 404
    assert client.get("/phase3.js").status_code == 200
