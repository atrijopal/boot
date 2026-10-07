"""Everything the model says is checked here, in code, for free.

  records : every quote must exist verbatim in the transcript (a neighbouring line is accepted only if the quote is verbatim there);
            one quote from the window being analysed; no number that is not in the cited text; an owner only from a first-person
            commitment by a labelled speaker, or a name/role that appears in the quote; a deadline only if it appears in a quote
  edits   : the Phase-2 gatekeeper (app/refinement/validate.py): verbatim and unique, no protected facts touched, no new digits,
            negations and numbers unchanged after the edit, and evidence required before an edit is applied
  names   : only self-introductions, with a verbatim quote spoken by that voice
"""
import re

from wordfreq import zipf_frequency

from app.core.lenses import LENSES
from app.core.schemas import Evidence, Record, SpeakerName
from app.core.units import Unit, segment_of
from app.preprocessing.common import WORD_RE
from app.preprocessing.datetimes import extract_dates, extract_durations, extract_times
from app.preprocessing.negation import extract_negations
from app.preprocessing.nlp import get_nlp
from app.preprocessing.numbers import extract_numbers
from app.refinement.validate import Support, check_edit
from app.schemas.refinement import Edit
from app.schemas.transcript import Transcript, TranscriptSegment

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
NAME_RE = re.compile(r"^[A-Z][A-Za-z'’\-\.]*(?: [A-Z][A-Za-z'’\-\.]*){0,3}$")


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
    """(start, end) of `quote` in `text`: exact first, then tolerant of quote/dash/whitespace variants only (never wording)."""
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote)
    nt, idx = _norm_with_map(text)
    nq, _ = _norm_with_map(quote.strip())
    j = nt.find(nq) if nq else -1
    return None if j < 0 else (idx[j], idx[j + len(nq) - 1] + 1)


# ---------------------------------------------------------------------------------------------- records
def _evidence(u: Unit, span: tuple[int, int], t: Transcript) -> Evidence:
    a, b = span
    covered = [k for k, (s, e) in zip(u.seg_idx, u.offsets) if s < b and e > a] or [segment_of(u, a)]
    segs = [t.segments[k] for k in covered]
    return Evidence(uid=u.uid, quote=u.text[a:b], start=segs[0].start, end=segs[-1].end, speaker=u.speaker, speaker_name=None,
                    segment_ids=[s.segment_id for s in segs], char_start=a, char_end=b)


def _joined_evidence(units: list[Unit], i: int, quote: str, t: Transcript, allowed: dict[str, Unit]) -> Evidence | None:
    """A quote that straddles two neighbouring lines: still verbatim in the transcript, so accept it if it matches the two lines joined."""
    for j in (i - 1, i):
        if j < 0 or j + 1 >= len(units) or units[j].uid not in allowed or units[j + 1].uid not in allowed:
            continue
        a_u, b_u = units[j], units[j + 1]
        joined = a_u.text + " " + b_u.text
        span = locate(joined, quote)
        if span is None:
            continue
        shift = len(a_u.text) + 1
        offs = list(zip(a_u.seg_idx, a_u.offsets)) + [(k, (s0 + shift, e0 + shift)) for k, (s0, e0) in zip(b_u.seg_idx, b_u.offsets)]
        covered = [k for k, (s0, e0) in offs if s0 < span[1] and e0 > span[0]]
        if not covered:
            continue
        segs = [t.segments[k] for k in covered]
        return Evidence(uid=a_u.uid, quote=joined[span[0] : span[1]], start=segs[0].start, end=segs[-1].end, speaker=a_u.speaker if a_u.speaker == b_u.speaker else a_u.speaker,
                        speaker_name=None, segment_ids=[x.segment_id for x in segs], char_start=span[0], char_end=span[1])
    return None


def _quote_parts(quote: str) -> list[str]:
    """Pieces of a quote that skips words with '...' in the middle. Pieces of fewer than two words are ignored; a quote with no usable piece stays whole."""
    parts = [p.strip() for p in re.split(r"\s*(?:\.{2,}|…)\s*", quote) if len(p.split()) >= 2]
    return parts or [quote]


NEGATION_CUE = re.compile(r"\b(?:not|no|never|none|neither|nor|without|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't)\b|n't", re.I)


def _evidence_for(quote: str, uid: str, units: list[Unit], by_id: dict[str, Unit], order: dict[str, int], t: Transcript) -> tuple[Evidence, Unit, tuple[int, int] | None] | None:
    """Where a verbatim quote was really said: the cited line, a neighbouring line, a line that continues into the next, or the one line of the window that has it."""
    u = by_id[uid]
    span = locate(u.text, quote)
    if span is None and uid in order:  # the model often cites the neighbouring line: accept only if the quote is verbatim there
        i = order[uid]
        for j in sorted(range(max(0, i - 2), min(len(units), i + 3)), key=lambda j: abs(j - i)):
            sp = locate(units[j].text, quote)
            if sp is not None and units[j].uid in by_id:
                u, span = units[j], sp
                break
    if span is None:  # still not found: the model may have mis-numbered the line. Accept only a verbatim quote that appears in exactly ONE line of the window
        hits = [(x, sp) for x in by_id.values() if (sp := locate(x.text, quote)) is not None]
        if len(hits) == 1:
            u, span = hits[0]
    if span is None and uid in order:
        ev = _joined_evidence(units, order[uid], quote, t, by_id)
        return (ev, u, None) if ev is not None else None
    return (_evidence(u, span, t), u, span) if span is not None else None


ELLIPSIS = re.compile(r"^(?:\.{2,}|…)\s*|\s*(?:\.{2,}|…)$")


def ground_record(raw, units: list[Unit], by_id: dict[str, Unit], target_ids: set[str], t: Transcript) -> tuple[Record | None, str | None]:
    """Returns (record, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "malformed item"
    kind, status = str(raw.get("t", "")).lower(), str(raw.get("s", "")).lower()
    if kind not in LENSES:
        return None, f"unknown kind '{kind}'"
    if status not in LENSES[kind]["statuses"]:
        return None, f"status '{status}' not valid for {kind}"
    text = str(raw.get("x") or "").strip()
    if not 3 <= len(text) <= 500:
        return None, "empty or oversized text"
    q = raw.get("q")
    if not isinstance(q, list) or not 1 <= len(q) <= 4:
        return None, "needs 1-4 quotes"
    order = {u.uid: i for i, u in enumerate(units)}
    evidence: list[Evidence] = []
    for pair in q:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            return None, "a quote is malformed"
        uid, quote = str(pair[0]), ELLIPSIS.sub("", str(pair[1]).strip()).strip()  # models often add "..." around a quote; it is not part of what was said
        u = by_id.get(uid)
        if u is None or not quote:
            return None, "a quote cites an unknown line or is empty"
        found = []
        for part in _quote_parts(quote):  # a quote with "..." inside it skips words: every piece must itself be verbatim
            hit = _evidence_for(part, uid, units, by_id, order, t)
            if hit is None:
                return None, "a quote is not in the cited line"
            found.append(hit)
        for (_, u1, s1), (_, u2, s2) in zip(found, found[1:]):  # the words an ellipsis skipped must not hide a negation
            if s1 and s2 and u1.uid == u2.uid and s1[1] <= s2[0] and NEGATION_CUE.search(u1.text[s1[1] : s2[0]]):
                return None, "an ellipsis in a quote skips a negation"
        evidence += [ev for ev, _, _ in found]
    if not any(e.uid in target_ids for e in evidence):
        return None, "no quote from the analysed window"
    cited = " ".join(by_id[e.uid].text for e in evidence) + " " + " ".join(t.segments[int(sid.split("_")[1]) - 1].text for e in evidence for sid in e.segment_ids if sid.startswith("seg_"))
    fields = [text] + ([str(raw.get("v") or "")] if kind == "fact" else [])
    if any(not numbers_in(f) <= numbers_in(cited) for f in fields):
        return None, "introduces a number that is not in the cited text"

    attrs: dict = {}
    if kind == "action":
        owner = {"text": None, "basis": "unspecified", "speaker": None}
        basis = raw.get("o")
        if basis == "first":
            u = by_id.get(str(raw.get("ou")))
            if u is not None and u.uid in {e.uid for e in evidence} and u.speaker != "SPEAKER_UNKNOWN":
                owner = {"text": u.speaker, "basis": "first_person", "speaker": u.speaker}
        elif basis == "named":
            name = str(raw.get("on") or "").strip()
            if name and any(name.lower() in e.quote.lower() for e in evidence):
                owner = {"text": name, "basis": "named_in_quote", "speaker": None}
        deadline = str(raw.get("d") or "").strip()
        attrs = {"owner": owner, "deadline": deadline if deadline and any(deadline.lower() in e.quote.lower() for e in evidence) else None}
    elif kind == "fact":
        label, value = str(raw.get("l") or "").strip(), str(raw.get("v") or "").strip()
        if not label or not value:
            return None, "a fact needs a label and a value"
        attrs = {"label": label, "value": value}
    speakers = list(dict.fromkeys(e.speaker for e in evidence if e.speaker != "SPEAKER_UNKNOWN"))
    return Record(kind=kind, status=status, text=text, attrs=attrs, evidence=evidence, start=min(e.start for e in evidence), speakers=speakers), None


# ---------------------------------------------------------------------------------------------- speaker names
def ground_names(raw_names, by_id: dict[str, Unit], legend: dict[str, str]) -> tuple[list[SpeakerName], list[str]]:
    """A name is kept only if it appears in a quoted line and: (self) the speaker of that line says it about themselves, or
    (addressed) someone else says it and the named speaker answers within the next three lines."""
    out: list[SpeakerName] = []
    notes: list[str] = []
    keys = list(by_id)
    items = [r for r in raw_names if isinstance(r, dict)] if isinstance(raw_names, list) else []
    items.sort(key=lambda r: r.get("how") == "addressed")  # self-introductions win over names said by others
    for r in items:
        tag, name, uid, quote = str(r.get("t", "")), str(r.get("n", "")).strip(), str(r.get("u", "")), str(r.get("q", "")).strip()
        how = "addressed" if r.get("how") == "addressed" else "self"
        u = by_id.get(uid)
        if tag not in legend or u is None or not NAME_RE.match(name) or len(name) > 40:
            continue
        if how == "self" and u.tag != tag:
            continue
        if how == "addressed":
            i = keys.index(uid)
            if u.tag == tag or not any(by_id[k].tag == tag for k in keys[i + 1 : i + 4]):
                continue
        if locate(u.text, quote) is None or not re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", quote, re.I):
            continue
        if any(s.speaker == legend[tag] or s.name.lower() == name.lower() for s in out):
            notes.append(f"Speaker name '{name}' not used: another name or speaker already claims it.")
            continue
        out.append(SpeakerName(speaker=legend[tag], name=name, uid=uid, quote=quote, basis=how))
    return out, notes


ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-/ ]{1,38}$")


def ground_people(raw, by_id: dict[str, Unit], legend: dict[str, str], target_ids: set[str]) -> list[dict]:
    """Names (self or addressed, same checks as ground_names) and roles (only said by the speaker about themselves, verbatim in the quote)."""
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        tag, uid, quote = str(r.get("t", "")), str(r.get("u", "")), str(r.get("q", "")).strip()
        name, role = str(r.get("n") or "").strip(), str(r.get("r") or "").strip()
        u = by_id.get(uid)
        if (not name and not role) or tag not in legend or u is None or uid not in target_ids:
            continue
        good = ground_names([{"t": tag, "n": name, "u": uid, "q": quote, "how": r.get("how")}], by_id, legend)[0] if name else []
        role_ok = bool(role and ROLE_RE.match(role) and r.get("how") != "addressed" and u.tag == tag and locate(u.text, quote) is not None
                       and re.search(rf"(?<![A-Za-z0-9]){re.escape(role)}(?![A-Za-z0-9])", quote, re.I))
        if not good and not role_ok:
            continue
        out.append({"speaker": legend[tag], "name": good[0].name if good else None, "role": role if role_ok else None,
                    "basis": good[0].basis if good else "self", "quote": quote, "uid": uid, "start": u.start})
    return out


# ---------------------------------------------------------------------------------------------- edits
def protected_spans(t: Transcript) -> dict[str, list[tuple[int, int, str]]]:
    """Per segment: character spans of numbers, dates, times, durations and negation cues. Edits may never touch them."""
    nlp = get_nlp()
    docs = list(nlp.pipe([s.text for s in t.segments], batch_size=64))
    out: dict[str, list[tuple[int, int, str]]] = {}
    for seg, doc in zip(t.segments, docs):
        spans = []
        for it in extract_numbers(seg) + extract_dates(seg) + extract_times(seg) + extract_durations(seg) + extract_negations(seg, doc):
            if it.char_start is not None:
                spans.append((it.char_start, it.char_end, f"{it.type} '{it.surface}'"))
        out[seg.segment_id] = spans
    return out


def gate_edits(raw_edits, by_id: dict[str, Unit], t: Transcript, protected: dict[str, list[tuple[int, int, str]]]) -> list[Edit]:
    sup = Support(transcript=t, phase1_terms=set(), second_text={})
    taken: dict[str, list[tuple[int, int]]] = {}
    changed: dict[str, int] = {}
    out: list[Edit] = []
    seen = set()
    for r in raw_edits if isinstance(raw_edits, list) else []:
        if not isinstance(r, dict):
            continue
        u, find, rep, kind = by_id.get(str(r.get("u"))), r.get("find"), r.get("rep"), str(r.get("k", "")).lower()
        key = (r.get("u"), find, rep)
        if key in seen or u is None or not isinstance(find, str) or not isinstance(rep, str):
            continue
        seen.add(key)
        span = locate(u.text, find)
        if span is None:
            out.append(_rejected(u, t, find, rep, kind, "`find` text was not found verbatim"))
            continue
        if re.search(r"[.!?]", rep) and not re.search(r"[.!?]", find):
            out.append(_rejected(u, t, find, rep, kind, "the fix adds sentence punctuation, which changes how the line reads"))
            continue
        k = segment_of(u, span[0])
        seg = t.segments[k]
        s0, e0 = u.offsets[u.seg_idx.index(k)]
        if span[1] > e0 + 0 or span[0] < s0:
            out.append(_rejected(u, t, find, rep, kind, "the text to change spans two transcript lines"))
            continue
        tk = taken.setdefault(seg.segment_id, [])
        status, why, support, sim, sp = check_edit(seg, {"find": seg.text[span[0] - s0 : span[1] - s0], "replace": rep, "kind": kind}, sup,
                                                  protected.get(seg.segment_id, []), tk, changed.get(seg.segment_id, 0), len(tk))
        if status != "rejected" and sp:
            tk.append(sp)
            changed[seg.segment_id] = changed.get(seg.segment_id, 0) + max(len(find), len(rep))
        out.append(Edit(edit_id="", segment_id=seg.segment_id, start=seg.start, end=seg.end, kind=kind, find=find, replace=rep,
                        char_start=sp[0] if sp else None, char_end=sp[1] if sp else None, status=status, reject_reason=why, support=support, similarity=sim))
    for i, e in enumerate(out, 1):
        e.edit_id = f"edt_{i:03d}"
    return out


def _rejected(u: Unit, t: Transcript, find, rep, kind, why) -> Edit:
    seg = t.segments[u.seg_idx[0]]
    return Edit(edit_id="", segment_id=seg.segment_id, start=seg.start, end=seg.end, kind=kind, find=str(find), replace=str(rep), status="rejected", reject_reason=why)


def apply_edits(t: Transcript, edits: list[Edit], names: dict[str, str]) -> Transcript:
    """Same segments (ids, times, speakers); only text changes where an edit was applied; speaker names added."""
    by_seg: dict[str, list[Edit]] = {}
    for e in edits:
        if e.status == "applied" and e.char_start is not None:
            by_seg.setdefault(e.segment_id, []).append(e)
    segs = []
    for s in t.segments:
        text, last = s.text, len(s.text) + 1
        for e in sorted(by_seg.get(s.segment_id, []), key=lambda x: -x.char_start):
            if e.char_end <= last and text[e.char_start : e.char_end] == e.find:
                text = text[: e.char_start] + e.replace + text[e.char_end :]
                last = e.char_start
        segs.append(s.model_copy(update={"text": text, "speaker_name": names.get(s.speaker) if s.speaker_known else None}))
    return t.model_copy(update={"kind": "refined", "segments": segs})


# ---------------------------------------------------------------------------------------------- hints (free)
def suspect_hints(t: Transcript, units: list[Unit], zipf_below: float = 3.0, limit: int = 20) -> list[tuple[str, str]]:
    """Words that are rare in general English (likely mis-hearings or jargon), as (unit id, word). Costs no tokens to compute."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for u in units:
        for m in WORD_RE.finditer(u.text):
            w = m.group().strip("'’-").lower()
            if len(w) >= 4 and w not in seen and zipf_frequency(w, "en") < zipf_below:
                seen.add(w)
                out.append((u.uid, w))
                if len(out) >= limit:
                    return out
    return out
