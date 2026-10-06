"""Evidence-backed event reconciliation, including returns across distant batches."""
import hashlib
from itertools import combinations

from app.schemas.phase3 import Relation
from app.summarization.llm import ask, pack
from app.summarization.validation import ground_refs, verify_records
from app.utils.errors import PipelineError


def relation_allowed(kind, source, target, positions):
    if source.event_id == target.event_id:
        return False
    if kind == "SUPERSEDES":
        return (source.kind == target.kind == "decision" and source.status in ("confirmed", "superseded")
                and target.status in ("confirmed", "superseded")
                and positions[source.anchor_unit_id] > positions[target.anchor_unit_id])
    if kind in ("ADOPTS", "REJECTS"):
        return source.kind == "decision" and source.status == "confirmed" and target.kind == "proposal"
    if kind == "RESOLVES":
        return source.kind in ("claim", "decision") and target.kind == "question"
    if kind == "DEPENDS_ON":
        return source.kind == "action" and target.kind in ("action", "decision")
    if kind == "CONTRADICTS":
        return source.kind in ("claim", "decision") and target.kind in ("claim", "decision")
    return False


def reconcile(events, units, model, verifier, run_id, max_chars, max_pairs=5000):
    supported = [e for e in events if e.validation_status == "supported"]
    by_event = {e.event_id: e for e in supported}
    by_unit = {u.unit_id: u for u in units}
    positions = {u.unit_id: i for i, u in enumerate(units)}
    pairs = []
    kinds = ("SUPERSEDES", "ADOPTS", "REJECTS", "RESOLVES", "DEPENDS_ON", "CONTRADICTS")
    # Outcomes compare globally: a topic split must not hide a revised decision.
    for a, b in combinations(supported, 2):
        same_topic = bool(set(a.topic_ids) & set(b.topic_ids))
        outcomes = a.kind in ("decision", "proposal", "action", "question") and b.kind in ("decision", "proposal", "action", "question")
        if not same_topic and not outcomes:
            continue
        if any(relation_allowed(k, a, b, positions) or relation_allowed(k, b, a, positions) for k in kinds):
            pairs.append({"left": a.model_dump(), "right": b.model_dump()})
        if len(pairs) > max_pairs:
            raise PipelineError("PHASE3_RELATION_LIMIT", "Too many event pairs for this run; increase PHASE3_MAX_RELATION_PAIRS after reviewing cost.")
    relations, issues = [], []
    for batch in pack(pairs, max_chars):
        response = ask("relations", {"pairs": batch}, model, run_id)
        if not isinstance(response.get("relations"), list):
            raise PipelineError("PHASE3_BAD_RESPONSE", "The relation extractor omitted its relation list.")
        allowed_pairs = {frozenset((p["left"]["event_id"], p["right"]["event_id"])) for p in batch}
        candidates = []
        for raw in response["relations"]:
            try:
                r = Relation.model_validate(raw)
                if frozenset((r.source, r.target)) not in allowed_pairs:
                    raise ValueError("Relation endpoints are not in this batch")
                if not relation_allowed(r.kind, by_event[r.source], by_event[r.target], positions):
                    raise ValueError("Invalid relation type or chronology")
                r.evidence = ground_refs(r.evidence, by_unit)
                allowed_units = {ref.unit_id for eid in (r.source, r.target) for ref in by_event[eid].evidence}
                if any(ref.unit_id not in allowed_units for ref in r.evidence):
                    raise ValueError("Relation evidence outside supplied endpoints")
                r.relation_id = "relation_" + hashlib.sha256(f"{r.kind}|{r.source}|{r.target}".encode()).hexdigest()[:16]
                candidates.append(r)
            except (ValueError, TypeError, KeyError) as err:
                issues.append("Rejected relation: " + str(err).splitlines()[0][:160])
        candidates = list({r.relation_id: r for r in candidates}.values())
        payload = [{"id": r.relation_id, "relation": r.model_dump(), "source": by_event[r.source].model_dump(),
                    "target": by_event[r.target].model_dump()} for r in candidates]
        verdicts = verify_records(payload, "relations", verifier, run_id, max_chars)
        for r in candidates:
            if verdicts[r.relation_id].get("supported") is True:
                relations.append(r)
            else:
                issues.append(f"{r.relation_id}: relation lacks semantic support")
    relations = sorted({r.relation_id: r for r in relations}.values(), key=lambda r: positions[by_event[r.source].anchor_unit_id])
    # Apply statuses only after the whole reconciliation pass succeeds.
    for r in relations:
        if r.kind == "SUPERSEDES":
            by_event[r.target].status = "superseded"
        elif r.kind == "ADOPTS":
            by_event[r.target].status = "adopted"
        elif r.kind == "REJECTS":
            by_event[r.target].status = "rejected"
        elif r.kind == "RESOLVES":
            by_event[r.target].status = "resolved"
    return relations, issues
