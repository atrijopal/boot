"""HTTP API: upload audio, run the three phases (one by one, or chained in one run), poll status, download results. UI is served from web/."""
import json
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.asr.registry import PROVIDERS
from app.config.settings import ROOT, settings
from app.preprocessing.pipeline import run_phase1
from app.minutes.pipeline import latest_folder, load_minutes, run_minutes
from app.minutes.render import to_export
from app.refinement.pipeline import job_dir, load_refinement, run_refinement, set_decision, set_manual_name, set_name_decision
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("api")
app = FastAPI(title="Meeting AI")
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
pool = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}
lock = threading.Lock()


IDLE = (None, "done", "failed", "skipped")  # sub-job stages in which a new run of that phase may start


def _set(job_id: str, **kw):
    with lock:
        jobs[job_id].update(kw)


def _skip(job_id: str, keys: tuple[str, ...], reason: str):
    """Later phases of a chained run that will not start because an earlier phase failed."""
    _set(job_id, **{k: {"stage": "skipped", "message": reason} for k in keys})


def _run_refine(job_id: str) -> bool:
    _set(job_id, refine={"stage": "starting", "message": "Starting refinement"})
    try:
        res = run_refinement(job_id, status=lambda st, msg: _set(job_id, refine={"stage": st, "message": msg}))
        _set(job_id, refine={"stage": "done", "message": "Finished", "result": res.model_dump()})
        return True
    except PipelineError as e:
        log.warning("refine %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, refine={"stage": "failed", "message": e.message, "error": {"code": e.code, "message": e.message}})
    except Exception:
        log.exception("refine %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, refine={"stage": "failed", "message": msg, "error": {"code": "INTERNAL_ERROR", "message": msg}})
    return False


def _run_phase1(job_id: str, path: Path, provider: str | None) -> bool:
    try:
        res = run_phase1(path, job_id=job_id, provider_name=provider, status=lambda st, msg: _set(job_id, stage=st, message=msg))
        _set(job_id, stage="done", message="Finished", result=res.model_dump())
        return True
    except PipelineError as e:
        log.warning("job %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, stage="failed", message=e.message, error={"code": e.code, "message": e.message})
    except Exception:  # never leak a stack trace to the UI
        log.exception("job %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, stage="failed", message=msg, error={"code": "INTERNAL_ERROR", "message": msg})
    return False


def _run(job_id: str, path: Path, provider: str | None, chain: tuple[str, ...] = ()):
    """Phase 1, then the phases in `chain` ("refine", "minutes") in order, in this one worker.
    Each phase starts only if the previous one finished; the ones that cannot start are marked "skipped" with the reason."""
    if not _run_phase1(job_id, path, provider):
        _skip(job_id, chain, "Not started: transcription (step 1) failed.")
        return
    if "refine" in chain and not _run_refine(job_id):
        _skip(job_id, tuple(k for k in chain if k != "refine"), "Not started: refinement (step 2) failed.")
        return
    if "minutes" in chain:
        _run_minutes_job(job_id)


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
async def process_audio(file: UploadFile = File(...), provider: str = Form(""), run_all: bool = Form(False), auto_refine: bool = Form(False)):
    """run_all: transcribe, refine and write the minutes in one run (what the page does by default).
    auto_refine (older flag): transcribe and refine only. Without either flag only Phase 1 runs and the rest is started per phase."""
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
    chain = ("refine", "minutes") if run_all else ("refine",) if auto_refine else ()
    jobs[job_id] = {"job_id": job_id, "stage": "queued", "message": "Queued", "filename": file.filename, "run_all": run_all,
                    # queued up front, so the page can show what will follow and the per-phase buttons cannot start a second copy
                    **{k: {"stage": "queued", "message": "Waiting for the previous step"} for k in chain}}
    pool.submit(_run, job_id, dest, provider.lower() or None, chain)
    return {"job_id": job_id}


@app.get("/jobs")
def list_jobs(limit: int = 40):
    """Earlier results found on disk, newest first (so a result can be reopened after a restart)."""
    out = []
    base = Path(settings.outputs_dir)
    for d in base.iterdir() if base.is_dir() else []:
        f = d / "phase1_result.json"
        if not f.is_file() or d.name in ("cache", "logs"):
            continue
        meta = {}
        try:
            meta = json.loads((d / "audio_metadata.json").read_text())
        except (OSError, ValueError):
            try:  # older runs: read it from the full Phase 1 result
                meta = json.loads(f.read_text()).get("audio_metadata", {})
            except (OSError, ValueError):
                pass
        out.append({"job_id": d.name, "file": meta.get("original_file", ""), "duration_seconds": meta.get("duration_seconds"),
                    "has_refinement": (d / "refinement.json").is_file(), "has_minutes": (d / "minutes" / "latest.json").is_file(), "mtime": f.stat().st_mtime})
    return sorted(out, key=lambda j: -j["mtime"])[: max(1, min(limit, 200))]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    return JSONResponse(_ensure_job(job_id))


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
            r = Path(settings.outputs_dir) / job_id / "refinement.json"
            if r.is_file():
                jobs[job_id]["refine"] = {"stage": "done", "message": "Finished", "result": json.loads(r.read_text())}
            try:
                m, stale = load_minutes(job_id)
                jobs[job_id]["minutes"] = {"stage": "done", "message": "Finished", "result": {**to_export(m), "stale": stale}}
            except PipelineError:
                pass
        return jobs[job_id]


@app.post("/jobs/{job_id}/refine")
def refine(job_id: str):
    job = _ensure_job(job_id)
    if job.get("stage") != "done":
        raise HTTPException(409, "Phase 1 is not finished for this job.")
    if job.get("refine", {}).get("stage") not in IDLE:
        raise HTTPException(409, "Refinement is already running or queued for this job.")
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


def _run_minutes_job(job_id: str):
    _set(job_id, minutes={"stage": "starting", "message": "Starting", "done": [], "running": []})
    done: set[str] = set()
    running: set[str] = set()

    def progress(st: str, msg: str):
        if st.endswith(":done"):
            node = st[: -len(":done")]
            running.discard(node)
            done.add(node)
        else:
            running.add(st)
        _set(job_id, minutes={"stage": "running", "message": msg or st, "done": sorted(done), "running": sorted(running)})

    try:
        run_minutes(job_id, status=progress)
        m, stale = load_minutes(job_id)
        _set(job_id, minutes={"stage": "done", "message": "Finished", "result": {**to_export(m), "stale": stale}})
    except PipelineError as e:
        log.warning("minutes %s failed: %s %s", job_id, e.code, e.message)
        _set(job_id, minutes={"stage": "failed", "message": e.message, "error": {"code": e.code, "message": e.message}})
    except Exception:
        log.exception("minutes %s crashed", job_id)
        msg = f"Unexpected internal error. Reference: {job_id}. See outputs/logs/phase1.log."
        _set(job_id, minutes={"stage": "failed", "message": msg, "error": {"code": "INTERNAL_ERROR", "message": msg}})


@app.post("/jobs/{job_id}/minutes")
def start_minutes(job_id: str):
    job = _ensure_job(job_id)
    if not (job_dir(job_id) / "refinement.json").is_file():
        raise HTTPException(409, "Run Phase 2 (Refine transcript) before generating minutes.")
    if job.get("minutes", {}).get("stage") not in IDLE:
        raise HTTPException(409, "Minutes are already being generated or queued for this job.")
    _set(job_id, minutes={"stage": "queued", "message": "Queued"})
    pool.submit(_run_minutes_job, job_id)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/minutes")
def get_minutes(job_id: str):
    try:
        m, stale = load_minutes(job_id)
    except PipelineError as e:
        raise HTTPException(404, e.message)
    return JSONResponse({**to_export(m), "stale": stale})


@app.get("/jobs/{job_id}/minutes/stages")
def get_stages(job_id: str):
    """Every LLM call of the minutes run (input and raw output) plus our keep/drop decision on each result."""
    try:
        folder, _ = latest_folder(job_id)
    except PipelineError as e:
        raise HTTPException(404, e.message)
    f = folder / "stages.json"
    if not f.is_file():
        raise HTTPException(404, "No stage log for this run (it was generated before stage logging existed). Generate the minutes again.")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


MINUTES_FILES = {"stages": ("stages.json", "application/json"), "md": ("minutes.md", "text/markdown"), "json": ("minutes.json", "application/json"),
                 "csv": ("action_items.csv", "text/csv"), "trace": ("trace.jsonl", "application/x-ndjson")}


@app.get("/jobs/{job_id}/minutes/download")
def download_minutes(job_id: str, format: str = "md"):
    if format not in MINUTES_FILES:
        raise HTTPException(400, f"format must be one of {', '.join(MINUTES_FILES)}")
    try:
        folder, _ = latest_folder(job_id)
    except PipelineError as e:
        raise HTTPException(404, e.message)
    name, mime = MINUTES_FILES[format]
    f = folder / name
    if not f.is_file():
        raise HTTPException(404, "Not available.")
    return FileResponse(f, media_type=mime, filename=f"{job_id}_{name}")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")
