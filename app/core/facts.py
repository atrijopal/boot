"""Phase 1 extractions that cost no model tokens: numbers, dates, times, durations, negations, names, acronyms, rare words, speaker stats.

No domain vocabulary is hardcoded; everything is pattern- or language-statistics-based, so it works for any meeting topic.
"""
import re
from collections import defaultdict

from wordfreq import zipf_frequency

from app.preprocessing.common import WORD_RE, overlaps, span_times
from app.preprocessing.datetimes import extract_dates, extract_durations, extract_times
from app.preprocessing.entities import extract_entities
from app.preprocessing.negation import extract_negations
from app.preprocessing.nlp import parse_segments
from app.preprocessing.numbers import extract_numbers
from app.core.schemas import RareWord
from app.schemas.facts import Mention, ProtectedFacts, SpeakerStat, TermFact
from app.schemas.transcript import Transcript

ACRONYMS = [
    ("spelled_acronym", re.compile(r"\b(?:[A-Z]-){2,}[A-Z]\b|\b(?:[A-Z]\.){2,}|\b(?:[A-Z] ){2,}[A-Z]\b")),
    ("acronym", re.compile(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*\b|\b[A-Z]{2,}[a-z][A-Za-z0-9]*\b")),  # PyTorch, NAFNet
    ("acronym", re.compile(r"\b[A-Z]{2,}[0-9]*s?\b")),  # GPU, GPUs
    ("acronym", re.compile(r"\b[A-Za-z]+-?\d+[A-Za-z0-9]*\b")),  # GPT-4, H100
]


def _acronyms(t: Transcript, claimed: dict[str, list[tuple[int, int]]]) -> list[TermFact]:
    found: dict[str, TermFact] = {}
    for seg in t.segments:
        taken: list[tuple[int, int]] = []
        for kind, rx in ACRONYMS:
            for m in rx.finditer(seg.text):
                span = (m.start(), m.end())
                if any(overlaps(span, c) for c in claimed.get(seg.segment_id, []) + taken):
                    continue
                taken.append(span)
                term = re.sub(r"[^A-Z]", "", m.group()) if kind == "spelled_acronym" else m.group()
                t0, t1 = span_times(seg, *span)
                tf = found.setdefault(term, TermFact(term=term, kind=kind, sources=["pattern"], count=0, confidence="medium"))
                tf.count += 1
                tf.mentions.append(Mention(segment_id=seg.segment_id, start=round(t0, 3), end=round(t1, 3), char_start=span[0], char_end=span[1]))
    return sorted(found.values(), key=lambda x: (-x.count, x.term))


def rare_words(t: Transcript, zipf_below: float = 3.0, limit: int = 40) -> list[RareWord]:
    """Words that are rare in general English: likely jargon or mis-hearings (the refinement step reads these)."""
    seen: dict[str, RareWord] = {}
    for seg in t.segments:
        for m in WORD_RE.finditer(seg.text):
            w = m.group().strip("'’-").lower()
            if len(w) >= 4 and zipf_frequency(w, "en") < zipf_below:
                if w in seen:
                    seen[w].count += 1
                else:
                    seen[w] = RareWord(word=w, count=1, first_start=seg.start)
    return sorted(seen.values(), key=lambda x: x.first_start)[:limit]


def speaker_stats(t: Transcript) -> list[SpeakerStat]:
    agg: dict[str, SpeakerStat] = {}
    for s in t.segments:
        key = s.speaker if s.speaker_known else "SPEAKER_UNKNOWN"
        a = agg.setdefault(key, SpeakerStat(speaker=key, speaker_known=s.speaker_known, talk_seconds=0.0, segments=0, words=0))
        a.talk_seconds = round(a.talk_seconds + max(0.0, s.end - s.start), 2)
        a.segments += 1
        a.words += len(s.text.split())
    return sorted(agg.values(), key=lambda x: -x.talk_seconds)


def extract_facts(t: Transcript) -> ProtectedFacts:
    docs = parse_segments([s.text for s in t.segments])
    facts = ProtectedFacts()
    claimed: dict[str, list[tuple[int, int]]] = {}
    for seg, doc in zip(t.segments, docs):
        dates, times, durs = extract_dates(seg), extract_times(seg), extract_durations(seg)
        temporal = [(f.char_start, f.char_end) for f in dates + times + durs]
        numbers = [n for n in extract_numbers(seg) if not any(overlaps((n.char_start, n.char_end), x) for x in temporal)]
        ents = extract_entities(seg, doc, temporal)
        facts.dates += dates
        facts.times += times
        facts.durations += durs
        facts.numbers += numbers
        for bucket, items in ents.items():
            getattr(facts, bucket).extend(items)
        facts.negations += extract_negations(seg, doc)
        claimed[seg.segment_id] = temporal + [(n.char_start, n.char_end) for n in numbers] + [
            (i.char_start, i.char_end) for b in ("people", "locations", "proper_noun_candidates") for i in ents[b]]
    facts.acronyms = _acronyms(t, claimed)
    return facts
