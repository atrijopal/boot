"""Verified topic claims, with an extractive fallback and complete registers."""
from app.schemas.phase3 import SummaryClaim, TopicSummary
from app.summarization.evidence import topic_packets
from app.summarization.llm import ask
from app.summarization.validation import numeric_fidelity, verify_records
from app.utils.errors import PipelineError


def registers(events):
    valid = [e for e in events if e.validation_status == "supported"]
    return {
        "decisions": [e for e in valid if e.kind == "decision"],
        "action_items": [e for e in valid if e.kind == "action"],
        "open_questions": [e for e in valid if e.kind == "question" and e.status != "resolved"],
        "risks": [e for e in valid if e.kind == "risk"],
    }


def _extractive(e):
    return SummaryClaim(text=f"[{e.kind}; {e.status}] {e.description}", event_ids=[e.event_id], evidence=e.evidence)


def make_summaries(topics, events, units, model, verifier, run_id, max_chars):
    by_event = {e.event_id: e for e in events}
    summaries, issues = [], []
    for t in topics:
        supported = [e for e in events if t.topic_id in e.topic_ids and e.validation_status == "supported"]
        claims = []
        if not supported:
            summaries.append(TopicSummary(topic_id=t.topic_id))
            continue
        try:
            packets = topic_packets(t.topic_id, events, units, max_chars)
            for part, packet in enumerate(packets):
                response = ask("summary", {"topic": t.title, "word_budget": max(30, t.narrative_words // len(packets)), "records": packet}, model, run_id)
                if not isinstance(response.get("claims"), list):
                    raise PipelineError("PHASE3_BAD_RESPONSE", "The summarizer omitted its claims.")
                allowed = {row["event"]["event_id"] for row in packet}
                candidates = []
                for raw in response["claims"]:
                    try:
                        if not isinstance(raw, dict) or set(raw) != {"text", "event_ids"}:
                            raise ValueError("Unexpected summary fields")
                        c = SummaryClaim(**raw)
                        if not set(c.event_ids) <= allowed:
                            raise ValueError("Unknown or unsupported event ID")
                        c.event_ids = list(dict.fromkeys(c.event_ids))
                        c.evidence = [ref for eid in c.event_ids for ref in by_event[eid].evidence]
                        if not numeric_fidelity(c.text, c.evidence):
                            raise ValueError("Changed numeric value")
                        candidates.append(c)
                    except (ValueError, TypeError):
                        issues.append(f"{t.topic_id}: removed a malformed or ungrounded summary claim")
                records = [{"id": str(i), "claim": c.model_dump(), "records": [row for row in packet if row["event"]["event_id"] in c.event_ids]} for i, c in enumerate(candidates)]
                verdicts = verify_records(records, "summary", verifier, run_id, max_chars)
                for i, c in enumerate(candidates):
                    if verdicts[str(i)].get("supported") is True:
                        claims.append(c)
                    else:
                        issues.append(f"{t.topic_id}: removed a summary claim rejected by the verifier")
        except (PipelineError, ValueError) as err:
            issues.append(f"{t.topic_id}: using supported event extracts because narrative generation failed ({getattr(err, 'code', type(err).__name__)})")
        if not claims:
            claims = [_extractive(e) for e in supported]
        # Deterministic truncation affects prose only, never event registers.
        selected, count, seen = [], 0, set()
        for c in claims:
            key = (c.text.casefold(), tuple(sorted(c.event_ids)))
            size = len(c.text.split())
            if key not in seen and (not selected or count + size <= t.narrative_words):
                selected.append(c)
                count += size
                seen.add(key)
        summaries.append(TopicSummary(topic_id=t.topic_id, claims=selected, event_ids=[e.event_id for e in supported]))

    # Final synthesis uses verified canonical events, not repeated compression.
    # Including status explicitly avoids representing old choices as current.
    current = [e for e in events if e.validation_status == "supported" and
               ((e.kind == "decision" and e.status == "confirmed") or
                (e.kind == "action" and e.status in ("committed", "assigned")))]
    other = [e for e in events if e.validation_status == "supported" and
             ((e.kind == "question" and e.status == "open") or e.kind in ("risk", "claim"))]
    priority = {t.topic_id: t.priority for t in topics}
    rank = lambda e: max((priority.get(t, 0) for t in e.topic_ids), default=0)
    executive, words = [], 0
    for e in sorted(current, key=rank, reverse=True) + sorted(other, key=rank, reverse=True):
        c = _extractive(e)
        if executive and words + len(c.text.split()) > 220:
            continue
        executive.append(c)
        words += len(c.text.split())
    return summaries, executive, issues
