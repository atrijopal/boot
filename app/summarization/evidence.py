"""Build bounded topic evidence packets from canonical supported records."""
from app.summarization.llm import pack


def event_packet(event, by_unit):
    return {"event": event.model_dump(), "sources": [
        {"unit_id": uid, "speaker_id": by_unit[uid].speaker_id, "text": by_unit[uid].text}
        for uid in dict.fromkeys(r.unit_id for r in event.evidence)]}


def topic_packets(topic_id, events, units, max_chars):
    by_unit = {u.unit_id: u for u in units}
    rows = [event_packet(e, by_unit) for e in events if topic_id in e.topic_ids and e.validation_status == "supported"]
    return list(pack(rows, max_chars))

