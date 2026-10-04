"""Dates, times and durations: surface detection only. Relative expressions are NOT resolved."""
import re

from app.preprocessing.common import make_item, resolve_overlaps
from app.schemas.facts import FactItem
from app.schemas.transcript import TranscriptSegment

WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
MONTH = r"(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
ORD = r"(?:st|nd|rd|th)"
NUMWORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|fifty|sixty|ninety|a|an|couple of|few|several|half an?)"
UNITS = r"(?:minutes?|hours?|days?|weeks?|months?|quarters?|years?|sprints?)"
MONTH_CAP = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
AMPM = r"(?:[ap]\.m\.|[ap]m)"
CLOCKWORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"

# (kind, regex, priority, confidence)
DATE_PATTERNS = [
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 0, "high"),
    ("date", re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"), 0, "medium"),
    ("date", re.compile(rf"\b(?:the\s+)?\d{{1,2}}(?:{ORD})?\s+(?:of\s+)?{MONTH}(?:,?\s+\d{{4}})?", re.I), 1, "high"),
    ("date", re.compile(rf"\b{MONTH}\s+\d{{1,2}}(?:{ORD})?(?:,?\s+\d{{4}})?\b", re.I), 1, "high"),
    ("date", re.compile(rf"\b(?:(?:next|last|this|coming|every|early|late|mid)\s+)?{WEEKDAY}s?\b(?:\s+(?:morning|afternoon|evening|night))?", re.I), 2, "high"),
    ("date", re.compile(r"\b(?:next|last|this|coming)\s+(?:week|weekend|month|quarter|year|sprint)\b", re.I), 2, "high"),
    ("date", re.compile(r"\b(?:today|tomorrow|yesterday|tonight|the day after tomorrow)\b", re.I), 2, "high"),
    ("date", re.compile(r"\b(?:end of (?:the )?(?:day|week|month|quarter|year|sprint)|EOD|EOW|EOM)\b", re.I), 2, "high"),
    ("date", re.compile(r"\bQ[1-4](?:\s+(?:of\s+)?\d{4})?\b"), 2, "high"),
    ("date", re.compile(r"\b(?:first|second|third|fourth)\s+quarter\b", re.I), 2, "medium"),
    # month alone: only capitalized ("in May"), so the modal verb "may" is not matched
    ("date", re.compile(rf"\b(?:in|during|by|before|until|since|from|of|early|late|mid)\s+{MONTH_CAP}\b(?!\s+\d)"), 3, "medium"),
]

TIME_PATTERNS = [
    ("time", re.compile(rf"\b\d{{1,2}}:\d{{2}}(?::\d{{2}})?\s?{AMPM}?", re.I), 0, "high"),
    ("time", re.compile(rf"\b\d{{1,2}}\s?{AMPM}(?!\w)", re.I), 0, "high"),
    ("time", re.compile(rf"\b{CLOCKWORD}\s?{AMPM}(?!\w)", re.I), 0, "medium"),
    ("time", re.compile(rf"\b(?:\d{{1,2}}|{CLOCKWORD})\s+o'?clock\b", re.I), 0, "high"),
    ("time", re.compile(r"\b(?:(?:around|about|by|before|after|at)\s+)?(?:noon|midnight)\b", re.I), 1, "high"),
    ("time", re.compile(r"\b(?:this|tomorrow|yesterday)\s+(?:morning|afternoon|evening)\b", re.I), 1, "medium"),
]

DURATION_PATTERNS = [
    ("duration", re.compile(rf"\b(?:(?:within|in|for|over|after|about|around|another|next|last)\s+)?(?:\d+(?:\.\d+)?|{NUMWORD})(?:\s*-\s*|\s+)(?:more\s+)?{UNITS}\b", re.I), 0, "medium"),
]


def _extract(seg: TranscriptSegment, patterns, type_: str) -> list[FactItem]:
    spans = []
    for kind, rx, prio, conf in patterns:
        for m in rx.finditer(seg.text):
            spans.append((m.start(), m.end(), prio, (kind, conf)))
    out = []
    for cs, ce, _p, (kind, conf) in resolve_overlaps(spans):
        surface = seg.text[cs:ce].strip()
        approx = bool(re.match(r"(around|about)\b", surface, re.I))
        out.append(
            make_item(
                seg, type_, surface, cs, ce,
                confidence=conf, source="regex",
                extra={"resolved": False, **({"approximate": True} if approx else {})},
                surface=seg.text[cs:ce],
            )
        )
    return out


def extract_dates(seg: TranscriptSegment) -> list[FactItem]:
    return _extract(seg, DATE_PATTERNS, "date")


def extract_times(seg: TranscriptSegment) -> list[FactItem]:
    return _extract(seg, TIME_PATTERNS, "time")


def extract_durations(seg: TranscriptSegment) -> list[FactItem]:
    return _extract(seg, DURATION_PATTERNS, "duration")
