"""Phase 3 routes. Single-process worker; no external service or database needed."""
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.summarization.export import ARTIFACTS
from app.summarization.pipeline import latest_folder, load_result, run_phase3, snapshot
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

router = APIRouter()
pool = ThreadPoolExecutor(max_workers=1)
guard = threading.Lock()
active = {}
log = get_logger("phase3_api")


class SummaryRequest(BaseModel):
    source_revision: str | None = None


def http_error(err):
    return HTTPException(409 if err.code in ("PHASE3_STALE", "PHASE3_INPUT_INVALID") else 404, err.message)


def _worker(job_id, revision):
    def report(stage, message):
        with guard:
            active[job_id] = {"stage": stage, "message": message, "source_revision": revision}
    try:
        result = run_phase3(job_id, report, revision)
        report(result.manifest.status, "Finished" if result.manifest.status == "complete" else "Partial results; rerun to resume failed batches")
    except PipelineError as err:
        report("stale" if err.code == "PHASE3_STALE" else "failed", err.message)
    except Exception:
        log.exception("Phase 3 failed for %s", job_id)
        report("failed", "Phase 3 encountered an internal error. Check the application log.")


@router.post("/jobs/{job_id}/summarize")
def summarize(job_id: str, body: SummaryRequest = Body(default=SummaryRequest())):
    # Avoid reading a Phase 2 run that is still producing its accepted snapshot.
    from app.api.main import jobs
    if jobs.get(job_id, {}).get("refine", {}).get("stage") not in (None, "done", "failed"):
        raise HTTPException(409, "Wait for Phase 2 to finish.")
    try:
        _, revision = snapshot(job_id)
    except PipelineError as err:
        raise http_error(err)
    if body.source_revision is not None and body.source_revision != revision:
        raise HTTPException(409, "The transcript changed. Reload and generate minutes again.")
    with guard:
        state = active.get(job_id, {})
        if state.get("stage") not in (None, "complete", "partial", "stale", "failed"):
            raise HTTPException(409, "Phase 3 is already running for this job.")
        active[job_id] = {"stage": "queued", "message": "Queued", "source_revision": revision}
    pool.submit(_worker, job_id, revision)
    return {"job_id": job_id, "stage": "queued", "source_revision": revision}


@router.get("/jobs/{job_id}/summary")
def summary(job_id: str):
    try:
        _, revision = snapshot(job_id)
        with guard:
            state = dict(active.get(job_id, {}))
        if state and state.get("source_revision") != revision:
            return {"stage": "stale", "message": "The accepted transcript changed. Regenerate minutes.", "source_revision": revision}
        if state.get("stage") not in (None, "complete", "partial"):
            return state
        try:
            result = load_result(job_id)
        except PipelineError as err:
            if err.code in ("PHASE3_MISSING", "PHASE3_STALE"):
                return {"stage": "stale" if err.code == "PHASE3_STALE" else "not_started", "message": err.message, "source_revision": revision}
            raise
        return {"stage": result.manifest.status, "source_revision": revision, "result": result.model_dump()}
    except PipelineError as err:
        raise http_error(err)


@router.get("/jobs/{job_id}/topics")
def topics(job_id: str):
    try:
        result = load_result(job_id)
        return {"topics": [t.model_dump() for t in result.topics], "duration_accounting": result.duration_accounting,
                "membership_calibrated": False, "status": result.manifest.status}
    except PipelineError as err:
        raise http_error(err)


@router.get("/jobs/{job_id}/graph")
def graph(job_id: str, topic_id: str | None = None):
    try:
        result = load_result(job_id)
        graph = result.knowledge_graph
        if topic_id:
            if topic_id not in {t.topic_id for t in result.topics}:
                raise HTTPException(404, "Unknown topic.")
            ids = {topic_id} | {e.event_id for e in result.events if topic_id in e.topic_ids}
            base_ids = set(ids)
            for edge in graph.edges:
                if edge.source in base_ids or edge.target in base_ids:
                    ids |= {edge.source, edge.target}
            return {"nodes": [n.model_dump() for n in graph.nodes if n.node_id in ids],
                    "edges": [e.model_dump() for e in graph.edges if e.source in ids and e.target in ids]}
        return graph.model_dump()
    except PipelineError as err:
        raise http_error(err)


@router.get("/jobs/{job_id}/phase3/download/{artifact}")
def download(job_id: str, artifact: str):
    if artifact not in ARTIFACTS:
        raise HTTPException(400, "Unknown Phase 3 artifact.")
    try:
        folder = latest_folder(job_id)
    except PipelineError as err:
        raise http_error(err)
    name, mime = ARTIFACTS[artifact]
    return FileResponse(folder / name, media_type=mime, filename=f"{job_id}_{name}")

