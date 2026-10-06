"""Fractional speaker-time and event-aware narrative allocation."""
import math


def allocate_importance(units, topics, events, total_words=900):
    total = sum(u.duration_seconds or 0 for u in units)
    missing = sum(u.duration_seconds is None for u in units)
    unresolved = sum((u.duration_seconds or 0) * u.unresolved_weight for u in units)
    for t in topics:
        t.duration_seconds = sum((u.duration_seconds or 0) * u.memberships.get(t.topic_id, 0) for u in units)
        t.duration_share = t.duration_seconds / total if total else None
        related = [e for e in events if t.topic_id in e.topic_ids and e.validation_status == "supported"]
        decisions = sum(e.kind == "decision" and e.status == "confirmed" for e in related)
        actions = sum(e.kind == "action" and e.status in ("assigned", "committed") for e in related)
        open_items = sum((e.kind == "question" and e.status == "open") or e.kind == "risk" for e in related)
        t.priority = .35 * (t.duration_share or 0) + .30 * (1-math.exp(-decisions)) + .25 * (1-math.exp(-actions)) + .10 * (1-math.exp(-open_items))
    if topics:
        base = min(45, total_words // len(topics))
        remaining = total_words - base * len(topics)
        weight = sum(t.priority for t in topics)
        quotas = [remaining * (t.priority / weight if weight else 1 / len(topics)) for t in topics]
        for t, quota in zip(topics, quotas):
            t.narrative_words = base + int(quota)
        left = total_words - sum(t.narrative_words for t in topics)
        for i in sorted(range(len(topics)), key=lambda i: quotas[i] % 1, reverse=True)[:left]:
            topics[i].narrative_words += 1
    return {"known_speaker_seconds": total, "unresolved_seconds": unresolved,
            "unresolved_share": unresolved / total if total else None,
            "missing_duration_units": missing, "coverage_complete": missing == 0,
            "approximate": any(u.timing_precision != "word" for u in units),
            "share_basis": "known-duration speaker-time; may exceed wall-clock time"}

