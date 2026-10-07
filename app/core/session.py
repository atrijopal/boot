"""The meeting session: one small file per meeting that the backend owns and the page only displays.

It records which stage is running, which are done, what failed, and where the user has to act, so a page (or a restarted server) can pick the meeting up
again. Artifacts (transcripts, proposals, the report) live beside it in the same folder; this file only says what exists and in what state.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import settings

STAGES = ["audio", "transcription", "speakers", "refinement", "report"]
LABELS = {"audio": "Audio", "transcription": "Transcription", "speakers": "Speakers", "refinement": "Refinement", "report": "Meeting report"}
STATES = ("waiting", "processing", "completed", "needs_review", "failed")
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path(job_id: str) -> Path:
    return Path(settings.outputs_dir) / job_id / "session.json"


def new_stage() -> dict:
    return {"state": "waiting", "started_at": None, "finished_at": None, "seconds": None, "message": "", "error": None, "attempts": 0}


def create(job_id: str, filename: str, meeting_date: str | None, mode: str = "review") -> dict:
    s = {"id": job_id, "created_at": now(), "updated_at": now(), "filename": filename, "meeting_date": meeting_date, "mode": mode, "status": "uploaded",
         "awaiting": None, "audio": {}, "stages": {k: new_stage() for k in STAGES}, "events": []}
    save(job_id, s)
    return s


def overall(s: dict) -> str:
    """One word for the whole meeting, from its stages."""
    st = {k: v["state"] for k, v in s["stages"].items()}
    if any(v == "failed" for v in st.values()):
        return "failed"
    if st["report"] == "processing":
        return "generating_report"
    if st["report"] == "completed":
        return "report_ready"
    if st["refinement"] == "processing":
        return "refining"
    if st["refinement"] in ("completed", "needs_review"):
        return "refinement_ready"
    if st["speakers"] in ("completed", "needs_review"):
        return "speakers_ready"
    if st["transcription"] == "completed":
        return "transcript_ready"
    if st["audio"] == "processing" or st["transcription"] == "processing":
        return "transcribing"
    return "uploaded"


def _awaiting(s: dict) -> str | None:
    for k in ("speakers", "refinement"):
        if s["stages"][k]["state"] == "needs_review":
            return k
    return None


def save(job_id: str, s: dict) -> None:
    with _lock:
        s["updated_at"] = now()
        s["status"] = overall(s)
        s["awaiting"] = _awaiting(s)
        p = path(job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)  # never leaves a half-written file for a reader


def load(job_id: str) -> dict | None:
    with _lock:
        try:
            return json.loads(path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def update(job_id: str, fn) -> dict:
    """Read, change, write, under one lock."""
    with _lock:
        s = load(job_id) or derive(job_id)
        fn(s)
        save(job_id, s)
        return s


def event(s: dict, stage: str, kind: str, detail: str = "") -> None:
    s["events"].append({"t": now(), "stage": stage, "event": kind, "detail": detail[:300]})
    del s["events"][:-200]


# ---- stage transitions (each saves)
def begin(job_id: str, stage: str, message: str = "") -> dict:
    def f(s):
        st = s["stages"][stage]
        st.update(state="processing", started_at=now(), finished_at=None, seconds=None, message=message, error=None, attempts=st["attempts"] + 1)
        event(s, stage, "started", message)
    return update(job_id, f)


def progress(job_id: str, stage: str, message: str) -> None:
    def f(s):
        if s["stages"][stage]["state"] == "processing":
            s["stages"][stage]["message"] = message
    update(job_id, f)


def finish(job_id: str, stage: str, state: str = "completed", message: str = "") -> dict:
    def f(s):
        st = s["stages"][stage]
        st["state"], st["finished_at"], st["message"], st["error"] = state, now(), message, None
        if st["started_at"]:
            st["seconds"] = round(max(0.0, time.time() - datetime.fromisoformat(st["started_at"]).timestamp()), 1)
        event(s, stage, state, message)
    return update(job_id, f)


def fail(job_id: str, stage: str, error: dict) -> dict:
    def f(s):
        st = s["stages"][stage]
        st["state"], st["finished_at"], st["error"], st["message"] = "failed", now(), error, error["title"]
        if st["started_at"]:
            st["seconds"] = round(max(0.0, time.time() - datetime.fromisoformat(st["started_at"]).timestamp()), 1)
        event(s, stage, "failed", f"{error['code']}: {error['detail']}")
    return update(job_id, f)


def reset_from(job_id: str, stage: str) -> dict:
    """Mark `stage` and every later stage as waiting again (their old files stay until they are overwritten)."""
    def f(s):
        for k in STAGES[STAGES.index(stage):]:
            s["stages"][k].update(state="waiting", started_at=None, finished_at=None, seconds=None, message="", error=None)
        event(s, stage, "reset", f"from {stage}")
    return update(job_id, f)


# ---- sessions for meetings that predate this file: read from what is on disk
def derive(job_id: str) -> dict:
    d = Path(settings.outputs_dir) / job_id
    up = {}
    try:
        up = json.loads((d / "upload.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    s = {"id": job_id, "created_at": now(), "updated_at": now(), "filename": up.get("filename") or "Recording", "meeting_date": up.get("meeting_date"), "mode": "review",
         "status": "uploaded", "awaiting": None, "audio": {}, "stages": {k: new_stage() for k in STAGES}, "events": []}
    have = lambda n: (d / n).is_file()
    done = {"audio": have("phase1.json"), "transcription": have("phase1.json"), "speakers": have("phase2.json"), "refinement": have("phase2.json"), "report": have("result.json")}
    for k, ok in done.items():
        if ok:
            s["stages"][k].update(state="completed", message="")
    for name in ("phase1.json", "result.json"):  # what the recording was, from the saved results
        try:
            a = json.loads((d / name).read_text(encoding="utf-8")).get("audio") or {}
            s["audio"] = {"duration_seconds": a.get("duration_seconds"), "format": a.get("format"), "file_size_bytes": a.get("file_size_bytes"), "channels": a.get("original_channels")}
            if not up.get("filename") and a.get("original_file"):
                s["filename"] = a["original_file"]
            break
        except (OSError, ValueError):
            continue
    try:
        s["updated_at"] = datetime.fromtimestamp(max(p.stat().st_mtime for p in d.iterdir() if p.is_file()), timezone.utc).isoformat(timespec="seconds")
    except (OSError, ValueError):
        pass
    s["status"] = overall(s)
    s["awaiting"] = _awaiting(s)
    return s
