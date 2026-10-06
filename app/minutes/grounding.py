"""Deterministic gates: nothing the model says becomes an item unless it is anchored in the transcript.

Rules (all in code, no LLM):
  - every quote must exist in the cited segment (a loose match tolerates curly quotes / whitespace, never wording)
  - at least one quote must come from the chunk's TARGET segments
  - numbers in the statement must exist in the cited segments (digits or spoken numbers)
  - owner: first-person (only from the cited segment's labelled speaker) or a name that appears in a quote; else "unspecified"
  - deadline: only text that appears in a quote
"""
import re

from app.minutes.chunking import Chunk, display
from app.minutes.lenses import LENSES
from app.preprocessing.numbers import extract_numbers
from app.schemas.minutes import Evidence, Item
from app.schemas.transcript import TranscriptSegment

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def numbers_in(text: str) -> set[float]:
    """Numeric values in a text: digits plus spoken numbers ('twenty five percent' -> 25)."""
    vals: set[float] = set()
    for tok in NUM.findall(text):
        try:
            vals.add(float(tok.replace(",", "")))
        except ValueError:
            pass
    for n in extract_numbers(TranscriptSegment(segment_id="n", start=0, end=1, text=text)):
        try:
            if n.extra.get("parsed_value") is not None:
                vals.add(float(n.extra["parsed_value"]))
        except ValueError:
            pass
    return vals


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text.translate(_QUOTES)):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) of `quote` inside `text`: exact first, then tolerant of quote/dash/whitespace variants only."""
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote)
    nt, idx = _norm_with_map(text)
    nq, _ = _norm_with_map(quote.strip())
    j = nt.find(nq) if nq else -1
    if j < 0:
        return None
    return idx[j], idx[j + len(nq) - 1] + 1


def ground_item(raw: dict, allowed: tuple[str, ...], chunk: Chunk, seg_by_id: dict[str, TranscriptSegment]) -> tuple[Item | None, str | None]:
    """Returns (item, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "malformed item"
    lens = str(raw.get("lens") or (allowed[0] if len(allowed) == 1 else "")).lower()
    if lens not in allowed:
        return None, f"lens '{lens}' not allowed here"
    status = str(raw.get("status", "")).lower()
    if status not in LENSES[lens]["statuses"]:
        return None, f"status '{status}' not valid for {lens}"
    text = str(raw.get("text") or "").strip()
    if not 3 <= len(text) <= 500:
        return None, "empty or oversized text"
    raw_ev = raw.get("evidence")
    if not isinstance(raw_ev, list) or not 1 <= len(raw_ev) <= 4:
        return None, "needs 1-4 evidence quotes"
    target_ids = {s.segment_id for s in chunk.target}
    order = [s.segment_id for s in chunk.all]
    evidence: list[Evidence] = []
    for e in raw_ev:
        seg = seg_by_id.get(e.get("segment_id")) if isinstance(e, dict) else None
        quote = str(e.get("quote") or "").strip() if isinstance(e, dict) else ""
        if seg is None or not quote:
            return None, "evidence cites an unknown segment or has an empty quote"
        span = locate(seg.text, quote)
        if span is None:  # the model often cites the neighbouring segment: accept only if the quote is verbatim there
            i = order.index(seg.segment_id) if seg.segment_id in order else -1
            for j in sorted(range(max(0, i - 2), min(len(order), i + 3)), key=lambda j: abs(j - i)) if i >= 0 else []:
                cand = seg_by_id[order[j]]
                sp = locate(cand.text, quote)
                if sp is not None:
                    seg, span = cand, sp
                    break
        if span is None:
            return None, "a quote is not in the cited segment"
        evidence.append(Evidence(
            segment_id=seg.segment_id, quote=seg.text[span[0] : span[1]], start=seg.start, end=seg.end,
            speaker=seg.speaker if seg.speaker_known else "SPEAKER_UNKNOWN", speaker_name=seg.speaker_name if seg.speaker_known else None,
            char_start=span[0], char_end=span[1],
        ))
    if not any(e.segment_id in target_ids for e in evidence):
        return None, "no quote from the target segments"
    cited_text = " ".join(seg_by_id[e.segment_id].text for e in evidence)
    cited_numbers = numbers_in(cited_text)
    fields = [text] + ([str(raw.get("value") or "")] if lens == "fact" else [])
    if any(not numbers_in(f) <= cited_numbers for f in fields):
        return None, "introduces a number that is not in the cited text"

    attrs: dict = {}
    if lens == "action":
        owner = {"text": None, "basis": "unspecified", "speaker": None}
        basis = raw.get("owner_basis")
        if basis == "first_person":
            seg = seg_by_id.get(raw.get("owner_segment_id"))
            if seg is not None and seg.segment_id in {e.segment_id for e in evidence} and seg.speaker_known:
                owner = {"text": display(seg), "basis": "first_person", "speaker": seg.speaker}
        elif basis == "named_in_quote":
            name = str(raw.get("owner_text") or "").strip()
            if name and any(name.lower() in e.quote.lower() for e in evidence):
                owner = {"text": name, "basis": "named_in_quote", "speaker": None}
        deadline = str(raw.get("deadline_text") or "").strip()
        attrs = {"owner": owner, "deadline": deadline if deadline and any(deadline.lower() in e.quote.lower() for e in evidence) else None}
    elif lens == "fact":
        label, value = str(raw.get("label") or "").strip(), str(raw.get("value") or "").strip()
        if not label or not value:
            return None, "a fact needs a label and a value"
        attrs = {"label": label, "value": value, "unit": raw.get("unit") if isinstance(raw.get("unit"), str) else None, "flags": []}

    speakers = list(dict.fromkeys((e.speaker_name or e.speaker) for e in evidence if e.speaker != "SPEAKER_UNKNOWN"))
    return Item(lens=lens, text=text, status=status, attrs=attrs, evidence=evidence, start=min(e.start for e in evidence),
                speakers=speakers, chunk_ids=[chunk.chunk_id]), None
