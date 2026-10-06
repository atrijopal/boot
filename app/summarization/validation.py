"""Deterministic provenance gates followed by mandatory semantic verification."""
import re

from app.schemas.phase3 import SourceRef
from app.summarization.llm import ask, pack
from app.utils.errors import PipelineError


def ground_refs(refs, by_unit):
    out = []
    for ref in refs:
        u = by_unit.get(ref.unit_id)
        if u is None or not ref.quote.strip() or u.text.count(ref.quote) != 1:
            raise ValueError("Evidence must be a unique exact substring of a known unit")
        a = u.text.index(ref.quote)
        out.append(SourceRef(unit_id=u.unit_id, quote=ref.quote, supports_fields=ref.supports_fields,
                             segment_id=u.segment_id, start_char=u.start_char + a,
                             end_char=u.start_char + a + len(ref.quote), source_revision=u.source_revision))
    return out


def numeric_fidelity(text, evidence):
    # Exact numeric/currency/percentage forms; semantic verifier checks units,
    # associations, negation, spoken numbers, and modality as well.
    pattern = r"(?:[$₹€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*%)?"
    cited = " ".join(r.quote for r in evidence)
    return all(m.group().strip() in cited for m in re.finditer(pattern, text))


def gate_event(event, by_unit, target_ids):
    if event.anchor_unit_id not in target_ids or not any(r.unit_id == event.anchor_unit_id for r in event.evidence):
        raise ValueError("Event must be anchored to the target region")
    event.evidence = ground_refs(event.evidence, by_unit)
    for field in ("description", "status"):
        if not any(field in r.supports_fields for r in event.evidence):
            raise ValueError(f"Missing evidence for {field}")
    if not numeric_fidelity(event.description, event.evidence):
        raise ValueError("Description introduced or changed a numeric value")
    # Relationship-dependent statuses are established only in reconciliation.
    if event.status in ("superseded", "adopted", "resolved"):
        raise ValueError("Status requires a separately validated relation")
    event.validation_status = "pending"
    event.validation_notes = []
    event.topic_ids = sorted({t for r in event.evidence for t, w in by_unit[r.unit_id].memberships.items() if w >= .15})
    for field in ("owner_speaker_id", "owner_text", "deadline_text"):
        value = getattr(event, field)
        if value is None:
            continue
        refs = [r for r in event.evidence if field in r.supports_fields]
        valid = bool(refs)
        if field == "owner_speaker_id":
            # Ownership by ID is direct first-person attribution only.
            # Named third-party assignments use owner_text instead.
            valid = valid and any(by_unit[r.unit_id].speaker_id == value and
                                  re.search(r"\bI\b|\bI'll\b|\bI’m\b|\bI'm\b", r.quote, re.I) for r in refs)
        else:
            valid = valid and any(value in r.quote for r in refs)
        if not valid:
            setattr(event, field, None)
            event.validation_notes.append(f"Cleared {field}: no field-specific source evidence")
    return event


def verify_records(records, task, model, run_id, max_chars):
    """Every input gets a literal-boolean verdict. Missing records fail closed."""
    verdicts = {}
    for batch in pack(records, max_chars):
        response = ask("verify", {"task": task, "records": batch}, model, run_id)
        rows = response.get("verdicts")
        if not isinstance(rows, list):
            raise PipelineError("PHASE3_BAD_RESPONSE", "The verifier omitted its verdict list.")
        expected = {r["id"] for r in batch}
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in expected:
                continue
            if row["id"] in verdicts:
                raise PipelineError("PHASE3_BAD_RESPONSE", "The verifier returned duplicate verdict IDs.")
            verdicts[row["id"]] = row
        if not expected <= verdicts.keys():
            raise PipelineError("PHASE3_BAD_RESPONSE", "The verifier omitted a required verdict.")
    return verdicts


def verify_events(events, by_unit, model, run_id, max_chars):
    records = [{"id": e.event_id, "event": e.model_dump(),
                "sources": [{"unit_id": uid, "speaker_id": by_unit[uid].speaker_id, "text": by_unit[uid].text}
                            for uid in dict.fromkeys(r.unit_id for r in e.evidence)]} for e in events]
    verdicts = verify_records(records, "events", model, run_id, max_chars)
    for e in events:
        v = verdicts[e.event_id]
        if all(v.get(k) is True for k in ("description_supported", "kind_supported", "status_supported")):
            e.validation_status = "supported"
        else:
            e.validation_status = "needs_review"
            e.validation_notes.append("Semantic verifier did not support description, kind, or status")
        for field in ("owner_speaker_id", "owner_text", "deadline_text"):
            if getattr(e, field) is not None and v.get(field + "_supported") is not True:
                setattr(e, field, None)
                e.validation_notes.append(f"Cleared {field}: semantic support not established")
    return events


def validate_result(result):
    nodes = {n.node_id for n in result.knowledge_graph.nodes}
    if len(nodes) != len(result.knowledge_graph.nodes):
        raise PipelineError("PHASE3_INVARIANT", "Duplicate graph node IDs.")
    if any(e.source not in nodes or e.target not in nodes for e in result.knowledge_graph.edges):
        raise PipelineError("PHASE3_INVARIANT", "Graph has a dangling reference.")
    for u in result.units:
        if abs(sum(u.memberships.values()) + u.unresolved_weight - 1) > 1e-6:
            raise PipelineError("PHASE3_INVARIANT", "Topic memberships do not sum to one.")
    by_unit = {u.unit_id: u for u in result.units}
    for e in result.events:
        ground_refs(e.evidence, by_unit)
    for r in result.relations:
        ground_refs(r.evidence, by_unit)
    events = {e.event_id: e for e in result.events}
    for claim in [c for t in result.topic_summaries for c in t.claims] + result.executive_summary:
        if not claim.evidence or any(i not in events or events[i].validation_status != "supported" for i in claim.event_ids):
            raise PipelineError("PHASE3_INVARIANT", "Summary claim lacks supported events.")
        ground_refs(claim.evidence, by_unit)
    return {"structural_checks_passed": True, "membership_calibrated": False,
            "supported_events": sum(e.validation_status == "supported" for e in result.events),
            "review_events": sum(e.validation_status != "supported" for e in result.events),
            "semantic_verification": "model-assisted; not a factual accuracy guarantee"}

