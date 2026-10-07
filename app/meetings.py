"""The meeting workspace API. The backend owns the pipeline and its state; every route either reads that state or asks the pipeline to move.

POST /meetings                      upload a recording and start processing
GET  /meetings                      the meetings, newest first
GET  /meetings/{id}                 state of every stage, issues, which exports exist
GET  /meetings/{id}/{transcript|speakers|refinement|report|pipeline}
POST /meetings/{id}/continue        the user reviewed speakers / refinement: carry on
POST /meetings/{id}/retry           run a stage again
POST /meetings/{id}/edits ...       accept or reject transcript corrections; POST /speaker-names, /names/{id}
GET  /meetings/{id}/audio           the recording (with range requests, so the page can seek)
GET  /meetings/{id}/exports, /export/{key}
"""
import json
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from app.config.settings import settings
from app.core import corrections, exports, graph, session, views
from app.core.errors import describe
from app.core.pipeline import load_phase1
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("meetings")
router = APIRouter(prefix="/meetings")
pool = ThreadPoolExecutor(max_workers=2)
RUNNING: set[str] = set()
_lock = threading.Lock()
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _dir(job: str) -> Path:
    if not ID.fullmatch(job):
        raise HTTPException(404, "Meeting not found.")
    return Path(settings.outputs_dir) / job


def _must_exist(job: str) -> dict:
    d = _dir(job)
    if not d.is_dir():
        raise HTTPException(404, "Meeting not found.")
    return _heal(job)


def _heal(job: str) -> dict:
    """A stage that says it is running although nothing is running (the server restarted) is marked interrupted, so the page can offer a retry."""
    s = session.load(job) or session.derive(job)
    if job not in RUNNING:
        for k, st in s["stages"].items():
            if st["state"] == "processing":
                session.fail(job, k, describe({"code": "INTERRUPTED", "detail": "The server stopped while this step was running."}))
        s = session.load(job) or s
    return s


def _input_path(job: str) -> Path | None:
    for p in _dir(job).glob("input.*"):
        return p
    return None


def _run(job: str, fn) -> None:
    with _lock:
        if job in RUNNING:
            raise HTTPException(409, "This meeting is already being processed.")
        RUNNING.add(job)

    def work():
        try:
            fn()
        except Exception:  # noqa: BLE001 - the graph records its own failures; this is the last resort
            log.exception("meeting %s crashed outside a stage", job)
        finally:
            with _lock:
                RUNNING.discard(job)

    pool.submit(work)


def _start(job: str, until: str = "report") -> None:
    s = session.load(job) or session.derive(job)
    path = _input_path(job)
    _run(job, lambda: graph.run(job, str(path) if path else None, None, s.get("mode", "review"), until))


@router.post("")
async def create(file: UploadFile = File(...), meeting_date: str = Form(""), mode: str = Form("review")):
    if meeting_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting_date):
        raise HTTPException(400, "The meeting date must look like 2026-05-31.")
    if mode not in ("review", "auto"):
        raise HTTPException(400, "mode must be review or auto.")
    job = uuid.uuid4().hex[:12]
    d = Path(settings.outputs_dir) / job
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"input{Path(file.filename or '').suffix.lower()}"
    limit, size = settings.max_upload_mb * 1024 * 1024, 0
    with open(dest, "wb") as f:
        while block := await file.read(1 << 20):
            size += len(block)
            if size > limit:
                f.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"The file is larger than the {settings.max_upload_mb} MB limit. Upload a smaller or more compressed recording.")
            f.write(block)
    (d / "upload.json").write_text(json.dumps({"filename": file.filename, "meeting_date": meeting_date or None}))
    session.create(job, file.filename or "Recording", meeting_date or None, mode)
    _start(job)
    return {"id": job}


@router.get("")
def listing(limit: int = 60):
    base = Path(settings.outputs_dir)
    out = []
    for d in base.iterdir() if base.is_dir() else []:
        if not ID.fullmatch(d.name) or not ((d / "session.json").is_file() or (d / "phase1.json").is_file()):
            continue
        s = session.load(d.name) or session.derive(d.name)
        out.append({"id": d.name, "example": bool(s.get("example")), "filename": s["filename"], "date": exports.date_of(s), "status": s["status"], "duration_seconds": (s.get("audio") or {}).get("duration_seconds"), "updated_at": s["updated_at"]})
    return sorted(out, key=lambda m: m["updated_at"], reverse=True)[: max(1, min(limit, 200))]


@router.get("/{job}")
def detail(job: str):
    _must_exist(job)
    return views.meeting_view(job)


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except PipelineError as e:
        d = describe(e)
        raise HTTPException(404 if e.code in ("JOB_NOT_FOUND", "PHASE2_MISSING", "EDIT_NOT_FOUND", "SUGGESTION_NOT_FOUND", "EXPORT_NOT_READY") else 400, d["detail"] or d["title"])


@router.get("/{job}/transcript")
def transcript(job: str):
    s = _must_exist(job)
    if s["stages"]["transcription"]["state"] != "completed":
        raise HTTPException(409, "The transcript is not ready yet.")
    return {"raw": exports.raw_segments(job), "refined": exports.refined_segments(job) if s["stages"]["refinement"]["state"] in ("needs_review", "completed") else None}


@router.get("/{job}/speakers")
def speakers(job: str):
    s = _must_exist(job)
    if s["stages"]["transcription"]["state"] != "completed":
        raise HTTPException(409, "The transcript is not ready yet.")
    return views.speakers_view(job)


@router.get("/{job}/refinement")
def refinement(job: str):
    _must_exist(job)
    return views.refinement_view(job)


@router.get("/{job}/report")
def report(job: str):
    _must_exist(job)
    return views.report_view(job)


@router.get("/{job}/pipeline")
def pipeline(job: str, developer: bool = False):
    _must_exist(job)
    return views.pipeline_view(job, developer)


def _not_example(job: str) -> None:
    if (session.load(job) or {}).get("example"):
        raise HTTPException(409, "This is a saved example, so it can not be processed again. Upload a recording to have a new meeting record produced.")


@router.post("/{job}/continue")
def continue_(job: str, body: dict = Body(...)):
    """The user has reviewed `stage`; record it and run on to the next checkpoint."""
    _must_exist(job)
    _not_example(job)
    stage = str(body.get("stage", ""))
    if stage not in ("speakers", "refinement"):
        raise HTTPException(400, "Only the speakers and refinement steps wait for review.")
    _guard(graph.approve, job, stage)
    _start(job)
    return {"id": job}


@router.post("/{job}/retry")
def retry(job: str, body: dict = Body(...)):
    """Run `stage` again, and the stages after it. Earlier finished work is kept."""
    _must_exist(job)
    _not_example(job)
    stage = str(body.get("stage", ""))
    if stage == "audio":
        stage = "transcription"
    if stage in ("transcription",) and _input_path(job) is None:
        raise HTTPException(409, "The original recording is no longer available. Upload it again.")
    _guard(graph.retry, job, stage)
    if stage == "report":
        _start(job, "report")
    else:
        _start(job)
    return {"id": job}


@router.post("/{job}/edits/{edit_id}")
def edit_decision(job: str, edit_id: str, body: dict = Body(...)):
    _must_exist(job)
    _guard(corrections.decide, job, edit_id, str(body.get("decision", "")))
    return views.refinement_view(job)


@router.post("/{job}/edits")
def edit_bulk(job: str, body: dict = Body(...)):
    _must_exist(job)
    _guard(corrections.decide_all, job, str(body.get("action", "")))
    return views.refinement_view(job)


@router.post("/{job}/names/{suggestion_id}")
def name_decision(job: str, suggestion_id: str, body: dict = Body(...)):
    _must_exist(job)
    _guard(corrections.decide_name, job, suggestion_id, str(body.get("decision", "")))
    return views.speakers_view(job)


@router.post("/{job}/speaker-names")
def speaker_names(job: str, body: dict = Body(...)):
    _must_exist(job)
    names = body.get("names")
    if not isinstance(names, dict):
        raise HTTPException(400, 'Send {"names": {"SPEAKER_00": "Name"}}.')
    _guard(corrections.rename_speakers, job, {str(k): str(v) for k, v in names.items()})
    return views.speakers_view(job)


@router.get("/{job}/audio")
def audio(job: str):
    _must_exist(job)
    p = _input_path(job)
    if p is None:
        raise HTTPException(404, "The recording is no longer available.")
    return FileResponse(p)  # supports range requests, so the page can jump to a timestamp


@router.get("/{job}/exports")
def export_list(job: str):
    _must_exist(job)
    return exports.catalog(job)


@router.get("/{job}/export/{key}")
def export(job: str, key: str, inline: bool = False):
    _must_exist(job)
    data, name, mime = _guard(exports.build, job, key)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"'})
