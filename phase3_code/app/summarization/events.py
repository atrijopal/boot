"""Bounded extraction with target ownership, checkpoints, and explicit coverage."""
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.phase3 import Event
from app.summarization.llm import ask, pack
from app.summarization.validation import gate_event, verify_events
from app.utils.errors import PipelineError


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def extract_events(units, manifest, folder, model, verifier, max_chars, status):
    by_unit = {u.unit_id: u for u in units}
    positions = {u.unit_id: i for i, u in enumerate(units)}
    rows = [{"unit_id": u.unit_id, "speaker_id": u.speaker_id, "speaker_name": u.speaker_name, "text": u.text} for u in units]
    batches = list(pack(rows, max_chars, overhead=4500))
    events, issues = [], []
    for index, batch in enumerate(batches):
        batch_id = f"events_{index:04d}"
        checkpoint = folder / "checkpoints" / (batch_id + ".json")
        target = {r["unit_id"] for r in batch}
        start, end = positions[batch[0]["unit_id"]], positions[batch[-1]["unit_id"]]
        context = rows[max(0, start-1):start] + rows[end+1:end+2]
        status("extracting", f"Extracting and verifying event batch {index+1}/{len(batches)}")
        try:
            if checkpoint.is_file():
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                parsed = [Event.model_validate(e) for e in saved["events"]]
                issues.extend(saved["issues"])
            else:
                response = ask("events", {"target": batch, "context_only": context}, model, manifest.run_id)
                if not isinstance(response.get("events"), list):
                    raise PipelineError("PHASE3_BAD_RESPONSE", "The event extractor omitted its event list.")
                parsed, rejected = [], []
                for raw in response["events"]:
                    try:
                        event = gate_event(Event.model_validate(raw), by_unit, target)
                        # IDs come from canonical evidence, never from model guesses.
                        identity = [event.kind, event.anchor_unit_id, event.description, [(r.unit_id, r.quote) for r in event.evidence]]
                        event.event_id = "event_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:16]
                        parsed.append(event)
                    except (ValueError, TypeError, ValidationError) as err:
                        rejected.append(f"{batch_id}: rejected event ({str(err).splitlines()[0][:180]})")
                parsed = list({e.event_id: e for e in parsed}.values())
                verify_events(parsed, by_unit, verifier, manifest.run_id, max_chars)
                atomic_json(checkpoint, {"events": [e.model_dump() for e in parsed], "issues": rejected})
                issues.extend(rejected)
            events.extend(parsed)
            manifest.covered_unit_ids.extend(sorted(target))
        except (PipelineError, ValueError, OSError) as err:
            manifest.failed_batches.append(batch_id)
            issues.append(f"{batch_id}: incomplete ({err.code if isinstance(err, PipelineError) else type(err).__name__}); rerun to resume")
        atomic_json(folder / "manifest.json", manifest.model_dump())
    # Conservative deduplication: identical records by the same speaker only.
    # Paraphrases or changed statuses stay separate rather than risking a merge.
    merged = {}
    for event in events:
        key = (event.kind, event.status, event.description.casefold().strip(), event.owner_speaker_id,
               event.owner_text, event.deadline_text, event.validation_status, by_unit[event.anchor_unit_id].speaker_id)
        if key not in merged:
            merged[key] = event
        else:
            prior = merged[key]
            refs = {(r.unit_id, r.quote): r for r in prior.evidence}
            refs.update({(r.unit_id, r.quote): r for r in event.evidence})
            prior.evidence = list(refs.values())[:12]
            prior.topic_ids = sorted(set(prior.topic_ids) | set(event.topic_ids))
    return list(merged.values()), issues
