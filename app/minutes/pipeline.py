"""Phase 3 entry point: refined transcript -> many extraction calls -> verified items -> final report."""
import hashlib
import json
import re
import threading
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import settings
from app.minutes.graph import build_graph
from app.minutes.llm import Ctx, prompt_hashes
from app.minutes.render import actions_csv, to_export, to_markdown
from app.minutes.trace import Tracer
from app.refinement.pipeline import load_phase1, load_refinement
from app.schemas.minutes import Minutes, Participant, RunMeta
from app.schemas.transcript import Transcript
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("minutes")
StatusFn = Callable[[str, str], None]
_running: set[str] = set()
_lock = threading.Lock()


def minutes_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise PipelineError("BAD_JOB_ID", "Invalid job identifier.")
    return Path(settings.outputs_dir) / job_id / "minutes"


def source_revision(t: Transcript) -> str:
    """Identity of the exact refined text + speaker names the minutes were written from."""
    payload = [[s.segment_id, s.text, s.speaker, s.speaker_name] for s in t.segments]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def _models() -> dict[str, str]:
    return {"extract": settings.minutes_extract_model, "topics": settings.minutes_topics_model, "verify": settings.minutes_verify_model,
            "write": settings.minutes_write_model}


def _run_id(revision: str) -> str:
    cfg = {k: getattr(settings, k) for k in sorted(type(settings).model_fields) if k.startswith("minutes_")}
    return hashlib.sha256(json.dumps([revision, cfg, prompt_hashes(), settings.llm_base_url], sort_keys=True).encode()).hexdigest()[:16]


def _participants(t: Transcript) -> list[Participant]:
    acc: dict[str, Participant] = {}
    for s in t.segments:
        if not s.speaker_known:
            continue
        p = acc.setdefault(s.speaker, Participant(speaker=s.speaker, name=s.speaker_name, talk_seconds=0.0, segments=0))
        p.talk_seconds = round(p.talk_seconds + max(0.0, s.end - s.start), 1)
        p.segments += 1
        p.name = p.name or s.speaker_name
    return sorted(acc.values(), key=lambda p: -p.talk_seconds)


def run_minutes(job_id: str, status: StatusFn | None = None) -> Minutes:
    root = minutes_dir(job_id)
    with _lock:
        if job_id in _running:
            raise PipelineError("MINUTES_BUSY", "Minutes are already being generated for this job.")
        _running.add(job_id)
    try:
        return _run(job_id, root, status)
    finally:
        with _lock:
            _running.discard(job_id)


def _run(job_id: str, root: Path, status: StatusFn | None) -> Minutes:
    ref = load_refinement(job_id)
    p1 = load_phase1(job_id)
    refined = ref.refined_transcript
    if not refined.segments:
        raise PipelineError("NO_SPEECH", "There is no transcript to summarise.")
    if not ref.invariants.all_ok:
        raise PipelineError("MINUTES_INPUT_INVALID", "The refinement checks did not pass for this job; refusing to build minutes on it.")
    revision = source_revision(refined)
    run_id = _run_id(revision)
    folder = root / "runs" / run_id
    done = folder / "minutes.json"
    if settings.cache_enabled and done.is_file():
        saved = Minutes.model_validate_json(done.read_text(encoding="utf-8"))
        if saved.status in ("complete",):
            _point_latest(root, run_id, revision)
            return saved
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "trace.jsonl").write_text("", encoding="utf-8")
    tracer = Tracer(folder / "trace.jsonl", status)
    parts = _participants(refined)
    ctx = Ctx(job_id=job_id, tracer=tracer, transcript=refined, facts=p1.protected_facts,
              participants=", ".join(f"{p.name} ({p.speaker})" if p.name else p.speaker for p in parts))
    state = build_graph(ctx).invoke({"raw_items": [], "warnings": [], "stats": {}})

    stats = state.get("stats", {})
    warnings = list(dict.fromkeys(state.get("warnings", [])))
    failed = sum(v.get("failed_chunks", 0) for k, v in stats.items() if k.startswith("extract_") and isinstance(v, dict))
    verifier_down = any("verifier was unreachable" in w for w in warnings)
    partial = bool(failed or verifier_down or stats.get("summaries_fallback") or stats.get("executive_fallback"))
    verified, review = state.get("verified", []), state.get("review", [])
    minutes = Minutes(
        meta=RunMeta(job_id=job_id, run_id=run_id, source_revision=revision, created_at=datetime.now(timezone.utc).isoformat(),
                     duration_seconds=refined.duration_seconds or refined.segments[-1].end, segments=len(refined.segments),
                     chunks=int(stats.get("chunks", 0)), models=_models()),
        status="partial" if partial else "complete", participants=parts, executive_summary=state.get("executive", []),
        topics=state.get("topics", []), items=verified, needs_review=review, relations=state.get("relations", []),
        warnings=warnings, stats={**{k: v for k, v in Counter(i.lens for i in verified).items()}, "needs_review": len(review),
                                  "failed_chunks": failed, "topics": len(state.get("topics", []))},
        trace_summary=tracer.summary(),
    )
    from app.minutes.chunking import make_chunks
    ctx.log.meta = {"models": _models(), "efforts": {"extract": settings.minutes_extract_effort, "topics": settings.minutes_topics_effort,
                                                     "verify": settings.minutes_verify_effort, "write": settings.minutes_write_effort},
                    "extract_mode": settings.minutes_extract_mode, "prompt_hashes": prompt_hashes(),
                    "chunks": [{"chunk_id": c.chunk_id, "first": c.target[0].segment_id, "last": c.target[-1].segment_id, "start": c.target[0].start, "end": c.target[-1].end}
                               for c in make_chunks(refined, settings.minutes_chunk_segments, settings.minutes_context_segments)]}
    (folder / "stages.json").write_text(json.dumps(ctx.log.export(), indent=1, ensure_ascii=False), encoding="utf-8")
    _save(folder, minutes)
    _point_latest(root, run_id, revision)
    log.info("minutes %s done: %s items, status %s", job_id, len(verified), minutes.status)
    return minutes


def _save(folder: Path, m: Minutes) -> None:
    (folder / "minutes.json").write_text(json.dumps(to_export(m), indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "minutes.md").write_text(to_markdown(m), encoding="utf-8")
    (folder / "action_items.csv").write_text(actions_csv(m), encoding="utf-8")


def _point_latest(root: Path, run_id: str, revision: str) -> None:
    (root / "latest.json").write_text(json.dumps({"run_id": run_id, "source_revision": revision}), encoding="utf-8")


def latest_folder(job_id: str) -> tuple[Path, str]:
    root = minutes_dir(job_id)
    f = root / "latest.json"
    if not f.is_file():
        raise PipelineError("MINUTES_MISSING", "Generate the minutes for this job first.")
    meta = json.loads(f.read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-f0-9]{16}", meta.get("run_id", "")):
        raise PipelineError("MINUTES_BAD_STATE", "Invalid minutes metadata.")
    return root / "runs" / meta["run_id"], meta["source_revision"]


def load_minutes(job_id: str) -> tuple[Minutes, bool]:
    """Returns (minutes, stale). stale = the refined transcript or speaker names changed after these minutes were written."""
    folder, revision = latest_folder(job_id)
    m = Minutes.model_validate_json((folder / "minutes.json").read_text(encoding="utf-8"))
    current = source_revision(load_refinement(job_id).refined_transcript)
    return m, current != revision
