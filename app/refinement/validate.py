"""Deterministic gatekeeper for proposed edits. No LLM here: every rule is checked in code.

An edit becomes:
  rejected  - it breaks a rule (not verbatim, touches a protected fact, changes numbers/negation, too large, ...)
  suggested - valid, but not backed by evidence; a human decides
  applied   - valid AND backed by evidence (appears elsewhere / second ASR heard it / matches a Phase-1 term) AND similar to the original
"""
import difflib
import re
from dataclasses import dataclass, field

import jellyfish

from app.config.settings import settings
from app.preprocessing.datetimes import extract_dates, extract_durations, extract_times
from app.preprocessing.negation import extract_negations
from app.preprocessing.nlp import get_nlp
from app.preprocessing.numbers import extract_numbers
from app.schemas.facts import ProtectedFacts
from app.schemas.refinement import Edit, Evidence
from app.schemas.transcript import Transcript, TranscriptSegment

ALLOWED_KINDS = {"term", "acronym", "name", "spelling", "format"}
_ALNUM = re.compile(r"[^a-z0-9]+")


def alnum(s: str) -> str:
    return _ALNUM.sub("", s.lower())


# ---------------------------------------------------------------- fingerprints (invariants)
def fingerprint(text: str) -> dict:
    """Facts that must survive refinement, recomputed with the same Phase-1 extractors."""
    seg = TranscriptSegment(segment_id="fp", start=0, end=1, text=text)
    doc = get_nlp()(text)
    return {
        "numbers": sorted((n.extra.get("kind", ""), n.value) for n in extract_numbers(seg)),
        "dates_times": sorted(f.surface.lower().strip() for f in extract_dates(seg) + extract_times(seg) + extract_durations(seg)),
        "negations": sorted(n.surface.lower() for n in extract_negations(seg, doc)),
    }


def fingerprint_diff(before: dict, after: dict) -> list[str]:
    out = []
    for key, label in (("numbers", "numbers"), ("dates_times", "dates/times"), ("negations", "negations")):
        if before[key] != after[key]:
            out.append(f"{label} changed: {before[key]} -> {after[key]}")
    return out


# ---------------------------------------------------------------- protected spans
def protected_spans(facts: ProtectedFacts, segment_id: str) -> list[tuple[int, int, str]]:
    spans = []
    for bucket in ("numbers", "dates", "times", "durations", "negations"):
        for it in getattr(facts, bucket):
            if it.segment_id == segment_id and it.char_start is not None:
                spans.append((it.char_start, it.char_end, f"{it.type} '{it.surface}'"))
    return spans


# ---------------------------------------------------------------- support / similarity
def similarity(find: str, replace: str) -> float:
    a, b = alnum(find), alnum(replace)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # sequence ratio only: Jaro-Winkler scores unrelated short words too high (whale/turtle 0.58)
    sims = [difflib.SequenceMatcher(None, a, b).ratio()]
    if jellyfish.metaphone(a) and jellyfish.metaphone(a) == jellyfish.metaphone(b):
        sims.append(0.9)  # same consonant skeleton ("pee ess en are" ~ "PSNR")
    return round(max(sims), 3)


def _word_in(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text, re.I) is not None


@dataclass
class Support:
    transcript: Transcript  # normalized, all segments
    phase1_terms: set[str] = field(default_factory=set)  # lowercase terms/acronyms/entities found in Phase 1
    second_text: dict[str, str] = field(default_factory=dict)  # segment_id -> second ASR text for that segment


def build_phase1_terms(facts: ProtectedFacts) -> set[str]:
    out = {t.term.lower() for t in facts.acronyms + facts.technical_terms}
    for bucket in ("people", "organizations", "locations", "other_entities", "proper_noun_candidates"):
        out |= {i.value.lower() for i in getattr(facts, bucket)}
    return out


def gather_support(edit_find: str, replace: str, seg: TranscriptSegment, cs: int, ce: int, sup: Support) -> list[Evidence]:
    ev: list[Evidence] = []
    for other in sup.transcript.segments:
        text = other.text
        if other.segment_id == seg.segment_id:
            text = text[:cs] + " " * (ce - cs) + text[ce:]  # ignore the span being edited
        if _word_in(text, replace):
            ev.append(Evidence(kind="elsewhere", detail=f"'{replace}' also appears in {other.segment_id} at {other.start:.0f}s"))
            break
    heard = sup.second_text.get(seg.segment_id, "")
    if heard and _word_in(heard, replace):
        ev.append(Evidence(kind="second_asr", detail=f"the second ASR also heard '{replace}'"))
    if replace.lower() in sup.phase1_terms and alnum(replace) != alnum(edit_find):
        ev.append(Evidence(kind="phase1_term", detail=f"'{replace}' matches a term/name discovered in Phase 1"))
    return ev


# ---------------------------------------------------------------- the gate
def check_edit(
    seg: TranscriptSegment,
    raw: dict,
    sup: Support,
    protected: list[tuple[int, int, str]],
    taken: list[tuple[int, int]],
    changed_chars: int,
    n_edits_in_segment: int,
) -> tuple[str, str | None, list[Evidence], float | None, tuple[int, int] | None]:
    """Returns (status, reject_reason, support, similarity, (char_start, char_end))."""
    find, replace, kind = raw.get("find"), raw.get("replace"), str(raw.get("kind", "")).lower()

    def reject(msg, span=None):
        return "rejected", msg, [], None, span

    if not isinstance(find, str) or not isinstance(replace, str) or not find.strip():
        return reject("malformed edit")
    if kind not in ALLOWED_KINDS:
        return reject(f"kind '{kind}' is not allowed (allowed: {', '.join(sorted(ALLOWED_KINDS))})")
    if not replace.strip():
        return reject("deleting text is not allowed")
    if find == replace:
        return reject("no change")
    n = seg.text.count(find)
    if n == 0:
        return reject("`find` text was not found verbatim in the segment")
    if n > 1:
        return reject(f"`find` is ambiguous (appears {n} times in the segment)")
    cs = seg.text.index(find)
    ce = cs + len(find)
    span = (cs, ce)

    if len(find.split()) > settings.refine_max_words or len(replace.split()) > settings.refine_max_words:
        return reject(f"edit longer than {settings.refine_max_words} words", span)
    if len(replace) > 3 * len(find) + 10:
        return reject("replacement is much longer than the original", span)
    if set(re.findall(r"\d", replace)) - set(re.findall(r"\d", find)):
        return reject("replacement introduces digits that were not in the original", span)
    if kind == "format" and alnum(find) != alnum(replace):
        return reject("a 'format' edit may not change letters or digits", span)
    for a, b, what in protected:
        if cs < b and a < ce:
            return reject(f"overlaps a protected fact ({what})", span)
    if any(cs < b and a < ce for a, b in taken):
        return reject("overlaps another edit in the same segment", span)
    if n_edits_in_segment >= settings.refine_max_edits_per_segment:
        return reject("too many edits in one segment", span)
    # cumulative cap against wholesale rewrites; floor so short phrase fixes in short segments pass
    if changed_chars + max(len(find), len(replace)) > max(settings.refine_max_changed_fraction * len(seg.text), settings.refine_min_changed_chars):
        return reject(f"would change more than {int(settings.refine_max_changed_fraction * 100)}% of the segment", span)

    # invariants: recompute the protected facts on the edited text
    after = seg.text[:cs] + replace + seg.text[ce:]
    problems = fingerprint_diff(fingerprint(seg.text), fingerprint(after))
    if problems:
        return reject("; ".join(problems), span)

    support = gather_support(find, replace, seg, cs, ce, sup)
    sim = similarity(find, replace)
    has_evidence = any(e.kind in ("elsewhere", "second_asr", "phase1_term") for e in support)
    similar = sim >= settings.refine_min_similarity
    if similar:
        support.append(Evidence(kind="similarity", detail=f"sounds/looks like the original (score {sim})"))
    heard_by_second = any(e.kind == "second_asr" for e in support)
    if has_evidence and (similar or heard_by_second):
        return "applied", None, support, sim, span
    return "suggested", None, support, sim, span
