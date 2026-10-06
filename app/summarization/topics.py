"""Persistent registry with absolute novelty gates and stable soft assignments.

All previous topics remain candidates. No fixed-duration topic windows are used.
"""
import re
from collections import Counter

import numpy as np

from app.schemas.phase3 import Topic

FILLER = {"yes", "no", "okay", "ok", "thanks", "thank you", "hello", "hi", "right", "agreed", "sure", "hmm"}


def _label(texts):
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    words = Counter(w for t in texts for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", t.lower()) if w not in ENGLISH_STOP_WORDS)
    return " / ".join(w for w, _ in words.most_common(4)) or "Discussion"


def _normal(v):
    n = np.linalg.norm(v)
    return v / n if n else v


def group_topics(units, vectors, threshold=0.62, merge_threshold=0.92, temperature=0.12):
    if len(units) != len(vectors) or temperature <= 0:
        raise ValueError("Invalid topic inputs")
    registry = []
    for i, unit in enumerate(units):
        text = unit.text.strip(" .!?,").lower()
        if text in FILLER or not np.linalg.norm(vectors[i]):
            continue
        scores = [float(vectors[i] @ r["centroid"]) for r in registry]
        if not scores or max(scores) < threshold:
            registry.append({"id": f"topic_{len(registry)+1:03d}", "centroid": vectors[i].copy(), "indices": [i], "aliases": []})
        else:
            r = registry[int(np.argmax(scores))]
            r["indices"].append(i)
            r["centroid"] = _normal(vectors[r["indices"]].mean(axis=0))

    # Conservative global duplicate reconciliation; preserve aliases.
    for a in range(len(registry)):
        if not registry[a]["indices"]:
            continue
        for b in range(a + 1, len(registry)):
            left, right = registry[a], registry[b]
            if right["indices"] and float(left["centroid"] @ right["centroid"]) >= merge_threshold:
                left["indices"] += right["indices"]
                left["aliases"] += [right["id"], *right["aliases"]]
                left["centroid"] = _normal(vectors[left["indices"]].mean(axis=0))
                right["indices"] = []
    registry = [r for r in registry if r["indices"]]
    topics = [Topic(topic_id=r["id"], title=_label([units[i].text for i in r["indices"]]),
                    exemplar_unit_ids=[units[i].unit_id for i in r["indices"][:4]], alias_topic_ids=r["aliases"]) for r in registry]
    previous = None
    for i, u in enumerate(units):
        u.memberships, u.unresolved_weight = {}, 1.0
        # A short acknowledgement inherits only an immediately adjacent topic.
        # This associates discussion; it NEVER establishes agreement/decision status.
        if u.text.strip(" .!?,").lower() in FILLER:
            if previous and i and 0 <= u.start_seconds - units[i-1].end_seconds <= 12:
                u.memberships, u.unresolved_weight = dict(previous), 0.0
            previous = u.memberships
            continue
        scores = np.array([max(float(vectors[i] @ r["centroid"]),
                               max(float(vectors[i] @ vectors[j]) for j in r["indices"][:4])) for r in registry])
        eligible = np.where(scores >= threshold)[0]
        if len(eligible):
            logits = scores[eligible] / temperature
            mass = np.exp(logits - logits.max())
            mass /= mass.sum()
            u.memberships = {topics[j].topic_id: float(w) for j, w in zip(eligible, mass)}
            u.unresolved_weight = 0.0
        previous = u.memberships
    for t in topics:
        t.unit_ids = [u.unit_id for u in units if u.memberships.get(t.topic_id, 0) >= 0.15]
        occurrence = []
        for u in units:
            if u.unit_id in t.unit_ids:
                occurrence.append(u.unit_id)
            elif occurrence:
                t.occurrences.append(occurrence)
                occurrence = []
        if occurrence:
            t.occurrences.append(occurrence)
    return topics

