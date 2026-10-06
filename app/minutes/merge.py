"""Merge duplicates that come from overlapping chunks or repeated mentions; keep all evidence."""
import difflib
import re

from app.schemas.minutes import Item

_W = re.compile(r"[a-z0-9]+")


def _norm(s: str) -> str:
    return " ".join(_W.findall(s.lower()))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _dup(a: Item, b: Item) -> bool:
    if a.lens != b.lens:
        return False
    sim = similarity(a.text, b.text)
    shared = {e.segment_id for e in a.evidence} & {e.segment_id for e in b.evidence}
    if a.lens == "fact":  # several different figures often sit in one sentence: compare the values themselves
        return a.attrs.get("value") == b.attrs.get("value") and similarity(a.attrs.get("label", ""), b.attrs.get("label", "")) >= 0.6
    return (sim >= 0.8 and abs(a.start - b.start) <= 90) or (bool(shared) and sim >= 0.55)


def dedupe(items: list[Item]) -> list[Item]:
    items = sorted(items, key=lambda i: (i.start, i.lens))
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[j].start - items[i].start > 180:
                break
            if _dup(items[i], items[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[Item]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    out = []
    for g in groups.values():
        keep = max(g, key=lambda x: (len(x.evidence), -x.start)).model_copy(deep=True)
        seen = {(e.segment_id, e.quote) for e in keep.evidence}
        for other in g:
            for e in other.evidence:
                if (e.segment_id, e.quote) not in seen and len(keep.evidence) < 6:
                    keep.evidence.append(e)
                    seen.add((e.segment_id, e.quote))
            keep.chunk_ids = sorted(set(keep.chunk_ids) | set(other.chunk_ids))
            if keep.lens == "action" and keep.attrs["owner"]["basis"] == "unspecified" and other.attrs["owner"]["basis"] != "unspecified":
                keep.attrs["owner"] = other.attrs["owner"]
            if keep.lens == "action" and not keep.attrs.get("deadline") and other.attrs.get("deadline"):
                keep.attrs["deadline"] = other.attrs["deadline"]
        keep.evidence.sort(key=lambda e: e.start)
        keep.start = min(e.start for e in keep.evidence)
        keep.speakers = list(dict.fromkeys((e.speaker_name or e.speaker) for e in keep.evidence if e.speaker != "SPEAKER_UNKNOWN"))
        out.append(keep)
    return sorted(out, key=lambda i: (i.start, i.lens))
