"""Phase 3 orchestration with immutable run directories and revision guards."""
import hashlib
import importlib.metadata
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import settings
from app.refinement.pipeline import load_refinement
from app.schemas.phase3 import Manifest, Phase3Result, SCHEMA_VERSION
from app.summarization.embeddings import embed_units
from app.summarization.events import atomic_json, extract_events
from app.summarization.export import save_artifacts
from app.summarization.graph import build_graph
from app.summarization.importance import allocate_importance
from app.summarization.llm import prompt_hashes
from app.summarization.relations import reconcile
from app.summarization.summaries import make_summaries
from app.summarization.topics import group_topics
from app.summarization.units import build_units, source_revision
from app.summarization.validation import validate_result
from app.utils.errors import PipelineError
from app.utils.job_locks import job_lock


def phase3_dir(job_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise PipelineError("BAD_JOB_ID", "Invalid job identifier.")
    return Path(settings.outputs_dir) / job_id / "phase3"


def snapshot(job_id):
    phase3_dir(job_id)
    with job_lock(job_id):
        result = load_refinement(job_id)
        if not result.invariants.all_ok:
            raise PipelineError("PHASE3_INPUT_INVALID", "Phase 2 invariant checks did not pass.")
        return result, source_revision(result)


def configuration():
    values = {k: v for k, v in settings.model_dump().items() if k.startswith("phase3_")}
    values.update(model=settings.phase3_model or settings.llm_model,
                  verifier=settings.phase3_verify_model or settings.verify_model,
                  llm_base_url=settings.llm_base_url, spacy_model=settings.spacy_model)
    try:
        values["spacy_model_version"] = importlib.metadata.version(settings.spacy_model)
    except importlib.metadata.PackageNotFoundError:
        values["spacy_model_version"] = "unavailable"
    return values


def run_identity(revision, config, prompts):
    payload = [SCHEMA_VERSION, revision, config, prompts]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def run_phase3(job_id, status=None, expected_revision=None):
    # Separate from the snapshot lock, so Phase 2 can still accept edits mid-run.
    phase3_dir(job_id)
    run_lock = job_lock("phase3-run:" + job_id)
    if not run_lock.acquire(blocking=False):
        raise PipelineError("PHASE3_BUSY", "Phase 3 is already running for this job.")
    try:
        return _run_phase3(job_id, status, expected_revision)
    finally:
        run_lock.release()


def _run_phase3(job_id, status=None, expected_revision=None):
    say = status or (lambda stage, message: None)
    started = time.monotonic()
    source, revision = snapshot(job_id)
    if expected_revision is not None and revision != expected_revision:
        raise PipelineError("PHASE3_STALE", "The accepted transcript changed. Reload and generate minutes again.")
    config, prompts = configuration(), prompt_hashes()
    run_id = run_identity(revision, config, prompts)
    root = phase3_dir(job_id)
    folder = root / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "latest_attempt.json", {"run_id": run_id, "source_revision": revision})
    existing = folder / "phase3_result.json"
    if settings.cache_enabled and existing.is_file():
        saved = Phase3Result.model_validate_json(existing.read_text(encoding="utf-8"))
        if saved.manifest.status == "complete":
            with job_lock(job_id):
                if snapshot(job_id)[1] != revision:
                    raise PipelineError("PHASE3_STALE", "The accepted transcript changed during this request.")
                atomic_json(root / "latest.json", {"run_id": run_id, "source_revision": revision})
            return saved
    manifest = Manifest(job_id=job_id, run_id=run_id, source_revision=revision, configuration=config,
                        prompt_hashes=prompts, started_at=datetime.now(timezone.utc).isoformat())

    def progress(stage, message):
        manifest.stage = stage
        atomic_json(folder / "manifest.json", manifest.model_dump())
        say(stage, message)

    try:
        atomic_json(folder / "source_snapshot.json", source.model_dump())
        progress("units", "Building source references from the accepted transcript")
        units = build_units(source, settings.phase3_unit_chars)
        manifest.total_units = len(units)
        progress("topics", "Grouping discussion into persistent topics")
        vectors = embed_units(units, settings.phase3_embedding_backend)
        topics = group_topics(units, vectors, settings.phase3_topic_threshold,
                              settings.phase3_merge_threshold, settings.phase3_temperature)
        if settings.phase3_embedding_backend == "tfidf":
            manifest.warnings.append("TF-IDF is a lexical baseline; paraphrased topic returns may be missed.")
        else:
            manifest.warnings.append("Topics use mean spaCy word vectors; topic weights are uncalibrated and grouping needs review.")
        events, issues = extract_events(units, manifest, folder, config["model"], config["verifier"], settings.phase3_batch_chars, progress)
        manifest.warnings.extend(issues)
        progress("relations", "Checking decision revisions, conflicts, and dependencies")
        relations = []
        try:
            relations, issues = reconcile(events, units, config["model"], config["verifier"], run_id,
                                          settings.phase3_batch_chars, settings.phase3_max_relation_pairs)
            manifest.warnings.extend(issues)
        except PipelineError as err:
            manifest.failed_batches.append("relations")
            manifest.warnings.append(f"Relation reconciliation incomplete ({err.code}); current decisions may still conflict.")
        accounting = allocate_importance(units, topics, events, settings.phase3_summary_words)
        graph = build_graph(job_id, revision, units, topics, events, relations)
        atomic_json(folder / "knowledge_graph.json", graph.model_dump())
        progress("summaries", "Generating and verifying topic minutes")
        summaries, executive, issues = make_summaries(topics, events, units, config["model"], config["verifier"], run_id, settings.phase3_batch_chars)
        manifest.warnings.extend(issues)
        if any(e.validation_status != "supported" for e in events):
            manifest.warnings.append("Some extracted events require review and are excluded from the confirmed registers.")
        if accounting["approximate"]:
            manifest.warnings.append("Some durations are segment estimates; shares use known speaker-time and may include pauses.")
        manifest.status = "partial" if manifest.failed_batches else "complete"
        manifest.stage = "done"
        manifest.finished_at = datetime.now(timezone.utc).isoformat()
        manifest.elapsed_seconds = round(time.monotonic() - started, 3)
        result = Phase3Result(manifest=manifest, units=units, topics=topics, events=events, relations=relations,
                              knowledge_graph=graph, topic_summaries=summaries, executive_summary=executive,
                              duration_accounting=accounting, validation_report={})
        result.validation_report = validate_result(result)
        result.validation_report.update(processing_complete=manifest.status == "complete",
                                         covered_units=len(set(manifest.covered_unit_ids)), total_units=len(units),
                                         warnings=manifest.warnings)
        # Saving and latest-pointer publication share Phase 2's write lock.
        with job_lock(job_id):
            if snapshot(job_id)[1] != revision:
                manifest.status = "stale"
                atomic_json(folder / "manifest.json", manifest.model_dump())
                raise PipelineError("PHASE3_STALE", "The transcript changed during Phase 3. Regenerate minutes for the new revision.")
            save_artifacts(folder, result)
            atomic_json(root / "latest.json", {"run_id": run_id, "source_revision": revision})
        return result
    except Exception:
        if manifest.status != "stale":
            manifest.status = "failed"
        manifest.finished_at = datetime.now(timezone.utc).isoformat()
        atomic_json(folder / "manifest.json", manifest.model_dump())
        raise


def latest_folder(job_id):
    root = phase3_dir(job_id)
    pointer = root / "latest.json"
    if not pointer.is_file():
        raise PipelineError("PHASE3_MISSING", "Generate Phase 3 minutes first.")
    meta = json.loads(pointer.read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-f0-9]{24}", meta.get("run_id", "")):
        raise PipelineError("PHASE3_BAD_STATE", "Invalid Phase 3 run metadata.")
    if snapshot(job_id)[1] != meta["source_revision"]:
        raise PipelineError("PHASE3_STALE", "These minutes belong to an older transcript. Generate them again.")
    return root / "runs" / meta["run_id"]


def load_result(job_id):
    return Phase3Result.model_validate_json((latest_folder(job_id) / "phase3_result.json").read_text(encoding="utf-8"))
