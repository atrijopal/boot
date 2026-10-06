"""Phase 1 API: upload audio, poll job status, fetch result. UI is served from web/."""
import json
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.asr.registry import PROVIDERS
from app.config.settings import ROOT, settings
from app.preprocessing.pipeline import run_phase1
from app.refinement.pipeline import job_dir, load_refinement, run_refinement, set_decision, set_manual_name, set_name_decision
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("api")
app = FastAPI(title="Meeting AI - Phases 1–3")
from app.summarization.api import router as phase3_router
app.include_router(phase3_router)
pool = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}
lock = threading.Lock()


def _set(job_id: str, **kw):
    with lock:
        jobs[job_id].update(kw)


def _run_refine(job_id: str):
    _set(job_id, refine={"stage": "starting", "message": "Starting refinement"})
    try:
        res = run_refinement(job_id, status=lambda st, msg: _set(job_id, refine={"stage": st, "message": msg}))
        _set(job_id, refine={"stage": "done", "message": "Finished", "result": res.model_dump()})
    except PipelineError as e:
        log.warning("refine %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, refine={"stage": "failed", "message": e.message, "error": {"code": e.code, "message": e.message}})
    except Exception:
        log.exception("refine %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, refine={"stage": "failed", "message": msg, "error": {"code": "INTERNAL_ERROR", "message": msg}})


def _run(job_id: str, path: Path, provider: str | None, auto_refine: bool = False):
    try:
        res = run_phase1(path, job_id=job_id, provider_name=provider, status=lambda st, msg: _set(job_id, stage=st, message=msg))
        _set(job_id, stage="done", message="Finished", result=res.model_dump())
        if auto_refine:
            _run_refine(job_id)
    except PipelineError as e:
        log.warning("job %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, stage="failed", message=e.message, error={"code": e.code, "message": e.message})
    except Exception:  # never leak a stack trace to the UI
        log.exception("job %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, stage="failed", message=msg, error={"code": "INTERNAL_ERROR", "message": msg})


@app.get("/health")
def health():
    return {
        "status": "ok",
        "providers": {
            "groq": {"configured": bool(settings.groq_api_key)},
            "deepgram": {"configured": bool(settings.deepgram_api_key)},
        },
        "default_provider": settings.asr_provider,
    }


@app.post("/process-audio")
async def process_audio(file: UploadFile = File(...), provider: str = Form(""), auto_refine: bool = Form(False)):
    if provider and provider.lower() not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider '{provider}'. Available: {', '.join(PROVIDERS)}.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(settings.outputs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "").suffix.lower()
    dest = job_dir / f"input{ext}"  # the original upload is stored once and never modified
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with open(dest, "wb") as f:
        while block := await file.read(1 << 20):
            size += len(block)
            if size > limit:
                f.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
            f.write(block)
    jobs[job_id] = {"job_id": job_id, "stage": "queued", "message": "Queued", "filename": file.filename}
    pool.submit(_run, job_id, dest, provider.lower() or None, auto_refine)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    return JSONResponse(job)


@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    f = Path(settings.outputs_dir) / job_id / "phase1_result.json"
    if not f.is_file():
        raise HTTPException(404, "Result not available (job unfinished or failed).")
    return FileResponse(f, media_type="application/json", filename=f"phase1_{job_id}.json")


def _ensure_job(job_id: str) -> dict:
    """Jobs live in memory; after a server restart, rebuild a stub from disk so refinement still works."""
    with lock:
        if job_id not in jobs:
            f = Path(settings.outputs_dir) / job_id / "phase1_result.json"
            if not f.is_file():
                raise HTTPException(404, "Unknown job id (no Phase 1 result found).")
            jobs[job_id] = {"job_id": job_id, "stage": "done", "message": "Finished", "result": json.loads(f.read_text())}
        return jobs[job_id]


@app.post("/jobs/{job_id}/refine")
def refine(job_id: str):
    job = _ensure_job(job_id)
    if job.get("stage") != "done":
        raise HTTPException(409, "Phase 1 is not finished for this job.")
    if job.get("refine", {}).get("stage") not in (None, "done", "failed"):
        raise HTTPException(409, "Refinement is already running.")
    _set(job_id, refine={"stage": "queued", "message": "Queued"})
    pool.submit(_run_refine, job_id)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/refinement")
def get_refinement(job_id: str):
    try:
        return JSONResponse(json.loads(load_refinement(job_id).model_dump_json()))
    except PipelineError as e:
        raise HTTPException(404, e.message)


@app.post("/jobs/{job_id}/edits/{edit_id}")
def decide(job_id: str, edit_id: str, body: dict = Body(...)):
    _ensure_job(job_id)
    try:
        res = set_decision(job_id, edit_id, str(body.get("decision", "")))
    except PipelineError as e:
        raise HTTPException(409 if e.code in ("EDIT_REJECTED", "INVARIANT_VIOLATION") else 400, e.message)
    data = json.loads(res.model_dump_json())
    _set(job_id, refine={"stage": "done", "message": "Finished", "result": data})
    return JSONResponse(data)


@app.post("/jobs/{job_id}/speaker-names/{name_id}")
def decide_name(job_id: str, name_id: str, body: dict = Body(...)):
    _ensure_job(job_id)
    try:
        res = set_name_decision(job_id, name_id, str(body.get("decision", "")))
    except PipelineError as e:
        raise HTTPException(409 if e.code == "EDIT_REJECTED" else 400, e.message)
    data = json.loads(res.model_dump_json())
    _set(job_id, refine={"stage": "done", "message": "Finished", "result": data})
    return JSONResponse(data)


@app.post("/jobs/{job_id}/speakers/{speaker}/name")
def rename_speaker(job_id: str, speaker: str, body: dict = Body(...)):
    _ensure_job(job_id)
    try:
        res = set_manual_name(job_id, speaker, str(body.get("name", "")))
    except PipelineError as e:
        raise HTTPException(400, e.message)
    data = json.loads(res.model_dump_json())
    _set(job_id, refine={"stage": "done", "message": "Finished", "result": data})
    return JSONResponse(data)


REFINED_FILES = {"json": ("refinement.json", "application/json"), "txt": ("refined_transcript.txt", "text/plain"),
                 "diff": ("diff.txt", "text/plain"), "speakers": ("speakers.json", "application/json"), "csv": ("edits.csv", "text/csv"), "edits": ("edits.json", "application/json")}


@app.get("/jobs/{job_id}/refined")
def download_refined(job_id: str, format: str = "txt"):
    if format not in REFINED_FILES:
        raise HTTPException(400, f"format must be one of {', '.join(REFINED_FILES)}")
    name, mime = REFINED_FILES[format]
    f = job_dir(job_id) / name
    if not f.is_file():
        raise HTTPException(404, "Not available: refine this job first.")
    return FileResponse(f, media_type=mime, filename=f"{job_id}_{name}")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/phase3.js")
def phase3_script():
    return FileResponse(ROOT / "web" / "phase3.js", media_type="application/javascript")
