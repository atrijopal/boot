"""Conservative identity reuse: accepted speaker names only, no alias guessing."""
import hashlib


def person_id(name):
    # Names are labels, not globally unique identities. Namespace by speaker below.
    return hashlib.sha256(name.encode()).hexdigest()[:12]


def accepted_speakers(units):
    speakers = {}
    for u in units:
        if u.speaker_id:
            speaker = speakers.setdefault(u.speaker_id, {"name": u.speaker_name, "unit_ids": []})
            speaker["unit_ids"].append(u.unit_id)
    return speakers

