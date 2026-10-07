"""Small API: upload a recording, poll the job, read the result, download files."""
import json
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import re

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import meetings
from app.core import examples
from app.asr.registry import PROVIDERS, default_provider
from app.config.settings import ROOT, settings
from app.core import corrections, report
from app.core.pipeline import load_phase1, load_phase2, load_result, run_phase1, run_phase2, run_phase3
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("api")
app = FastAPI(title="Meeting Intelligence Assistant")
@app.on_event("startup")
def _seed_examples():
    examples.seed()  # saved example meetings (real earlier output) open without any processing


app.include_router(meetings.router)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
pool = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}
lock = threading.Lock()
FILES = {"steps": ("next_steps.md", "text/markdown"), "email": ("email.txt", "text/plain"), "ics": ("action_items.ics", "text/calendar"), "phase1": ("phase1.json", "application/json"), "phase2": ("phase2.json", "application/json"), "refined": ("refined_transcript.txt", "text/plain"), "html": ("report.html", "text/html"), "md": ("minutes.md", "text/markdown"), "json": ("minutes.json", "application/json"), "csv": ("action_items.csv", "text/csv"),
         "raw": ("raw_transcript.txt", "text/plain"), "txt": ("raw_transcript.txt", "text/plain"), "calls": ("calls.json", "application/json")}


def _set(job_id: str, **kw):
    with lock:
        jobs.setdefault(job_id, {"job_id": job_id}).update(kw)


def _payload(job_id: str) -> dict:
    """Whatever has been produced so far for a job, rebuilt from disk: phase1, phase2, result."""
    out: dict = {}
    for key, loader in (("phase1", load_phase1), ("phase2", load_phase2), ("result", load_result)):
        try:
            v = loader(job_id)
        except PipelineError:
            v = None
        if v is not None:
            out[key] = json.loads(v.model_dump_json())
    d = Path(settings.outputs_dir) / job_id
    if "result" in out and "phase2" in out:  # phase 2 was changed (re-run, or a fix accepted/rejected) after the minutes were made
        out["stale_result"] = (d / "phase2.json").stat().st_mtime > (d / "result.json").stat().st_mtime + 1
    return out


def _run(job_id: str, phases: list[int], path: Path | None = None, provider: str | None = None):
    """Runs the listed phases in order; every phase saves its own files, so a later one can be run on its own."""
    say = lambda s, m="": _set(job_id, stage=s, message=m)
    try:
        for n in phases:
            _set(job_id, phase=n)
            if n == 1:
                run_phase1(path, job_id=job_id, provider_name=provider, status=say)
            elif n == 2:
                run_phase2(job_id, status=say)
            else:
                run_phase3(job_id, status=say)
        _set(job_id, stage="done", message="Finished", **_payload(job_id))
    except PipelineError as e:
        log.warning("job %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, stage="failed", message=e.message, error={"code": e.code, "message": e.message}, **_payload(job_id))
    except Exception:  # never leak a stack trace
        log.exception("job %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, stage="failed", message=msg, error={"code": "INTERNAL_ERROR", "message": msg}, **_payload(job_id))


@app.get("/health")
def health():
    return {"status": "ok", "default_provider": default_provider(), "providers": {
        "groq": {"configured": bool(settings.groq_api_key)}, "deepgram": {"configured": bool(settings.deepgram_api_key)}}}


@app.post("/process")
async def process(file: UploadFile = File(...), provider: str = Form(""), through: int = Form(1), meeting_date: str = Form("")):
    """Uploads the recording and runs phase 1 (default) or phases 1..`through`."""
    if provider and provider.lower() not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider '{provider}'. Available: {', '.join(PROVIDERS)}.")
    if meeting_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting_date):
        raise HTTPException(400, "meeting_date must look like 2026-05-31.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(settings.outputs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / f"input{Path(file.filename or '').suffix.lower()}"  # the upload is stored once and never modified
    limit, size = settings.max_upload_mb * 1024 * 1024, 0
    with open(dest, "wb") as f:
        while block := await file.read(1 << 20):
            size += len(block)
            if size > limit:
                f.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
            f.write(block)
    (job_dir / "upload.json").write_text(json.dumps({"filename": file.filename, "meeting_date": meeting_date or None}))
    _set(job_id, stage="queued", message="Queued", filename=file.filename)
    pool.submit(_run, job_id, list(range(1, max(1, min(through, 3)) + 1)), dest, provider.lower() or None)
    return {"job_id": job_id}


@app.post("/jobs/{job_id}/phase/{n}")
def run_phase(job_id: str, n: int):
    """Runs phase 2 or 3 on a job whose earlier phase is already saved."""
    if n not in (2, 3):
        raise HTTPException(400, "Only phase 2 and 3 can be started here; phase 1 starts with the upload.")
    with lock:
        if jobs.get(job_id, {}).get("stage") not in (None, "done", "failed"):
            raise HTTPException(409, "This job is still running.")
    try:
        load_phase1(job_id)
    except PipelineError as e:
        raise HTTPException(404, e.message)
    _set(job_id, stage="queued", message=f"Queued phase {n}", phase=n, error=None)
    pool.submit(_run, job_id, [n])
    return {"job_id": job_id, "phase": n}


def _job(job_id: str) -> dict:
    with lock:
        if job_id in jobs:
            return jobs[job_id]
    pl = _payload(job_id) if job_id.replace("-", "").replace("_", "").isalnum() else {}
    if not pl:
        raise HTTPException(404, "Unknown job id.")
    return {"job_id": job_id, "stage": "done", "message": "Finished", **pl}  # after a restart: rebuilt from disk


@app.get("/jobs")
def list_jobs(limit: int = 40):
    out = []
    base = Path(settings.outputs_dir)
    for d in base.iterdir() if base.is_dir() else []:
        f = d / "phase1.json"
        if not f.is_file():
            f = d / "result.json"  # results saved before the phases existed
        if f.is_file():
            try:
                a = json.loads(f.read_text())["audio"]
                try:
                    name = json.loads((d / "upload.json").read_text())["filename"]
                except (OSError, ValueError, KeyError):
                    name = a.get("original_file", "")
                phase = 3 if (d / "result.json").is_file() else 2 if (d / "phase2.json").is_file() else 1
                out.append({"job_id": d.name, "file": name, "duration_seconds": a.get("duration_seconds"), "phase": phase, "mtime": f.stat().st_mtime})
            except (OSError, ValueError, KeyError):
                pass
    return sorted(out, key=lambda j: -j["mtime"])[: max(1, min(limit, 200))]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    return JSONResponse(_job(job_id))


@app.get("/jobs/{job_id}/download")
def download(job_id: str, format: str = "md"):
    if format not in FILES:
        raise HTTPException(400, f"format must be one of {', '.join(FILES)}")
    _job(job_id)
    name, mime = FILES[format]
    f = Path(settings.outputs_dir) / job_id / name
    if not f.is_file() and name == "raw_transcript.txt":
        f = Path(settings.outputs_dir) / job_id / "transcript.txt"  # jobs made before the raw transcript had its own file
    if not f.is_file():
        raise HTTPException(404, "Not available yet.")
    return FileResponse(f, media_type=mime, filename=f"{job_id}_{name}")


def _busy(job_id: str) -> None:
    with lock:
        if jobs.get(job_id, {}).get("stage") not in (None, "done", "failed"):
            raise HTTPException(409, "This job is still running.")


@app.post("/jobs/{job_id}/edits/{edit_id}")
def decide_edit(job_id: str, edit_id: str, body: dict = Body(...)):
    """Accept, reject or reset one transcript fix. A fix the code blocked cannot be accepted."""
    _busy(job_id)
    try:
        corrections.decide(job_id, edit_id, str(body.get("decision", "")))
    except PipelineError as e:
        raise HTTPException(404 if e.code in ("EDIT_NOT_FOUND", "JOB_NOT_FOUND", "PHASE2_MISSING") else 400, e.message)
    return _job_state(job_id)


@app.post("/jobs/{job_id}/names/{suggestion_id}")
def decide_name(job_id: str, suggestion_id: str, body: dict = Body(...)):
    """Accept, reject or reset one "who is who" suggestion from phase 2."""
    _busy(job_id)
    try:
        corrections.decide_name(job_id, suggestion_id, str(body.get("decision", "")))
    except PipelineError as e:
        raise HTTPException(404 if e.code in ("SUGGESTION_NOT_FOUND", "JOB_NOT_FOUND", "PHASE2_MISSING") else 400, e.message)
    return _job_state(job_id)


@app.post("/jobs/{job_id}/speakers")
def name_speakers(job_id: str, body: dict = Body(...)):
    """Type names for speaker labels ({"names": {"SPEAKER_A": "Dana"}}). Rebuilds the report without any model call."""
    _busy(job_id)
    names = body.get("names")
    if not isinstance(names, dict):
        raise HTTPException(400, 'Send {"names": {"SPEAKER_A": "Dana"}}.')
    try:
        corrections.rename_speakers(job_id, {str(k): str(v) for k, v in names.items()})
    except PipelineError as e:
        raise HTTPException(404 if e.code == "JOB_NOT_FOUND" else 400, e.message)
    return _job_state(job_id)


def _job_state(job_id: str) -> dict:
    with lock:
        jobs.pop(job_id, None)  # the saved files are the truth after a correction
    return _job(job_id)


@app.get("/jobs/{job_id}/report")
def report_page(job_id: str):
    """The finished report as a page (shown inside the main page)."""
    _job(job_id)
    f = Path(settings.outputs_dir) / job_id / "report.html"
    if not f.is_file():
        raise HTTPException(404, "Not available yet.")
    return FileResponse(f, media_type="text/html")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")

