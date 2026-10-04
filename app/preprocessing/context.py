"""MeetingContext: a compact, per-meeting summary of what was found (no LLM)."""
from collections import defaultdict

from app.schemas.facts import EntitySummary, MeetingContext, ProtectedFacts, SpeakerStat, TopicHint
from app.schemas.transcript import Transcript


def build_context(t: Transcript, facts: ProtectedFacts, topic_hints: list[TopicHint], notes: list[str]) -> MeetingContext:
    spk: dict[str, SpeakerStat] = {}
    for s in t.segments:
        st = spk.setdefault(s.speaker, SpeakerStat(speaker=s.speaker, speaker_known=s.speaker_known, talk_seconds=0, segments=0, words=0))
        st.talk_seconds = round(st.talk_seconds + max(0.0, s.end - s.start), 2)
        st.segments += 1
        st.words += len(s.text.split())

    ents: dict[tuple[str, str], EntitySummary] = {}
    for bucket in ("people", "organizations", "locations", "other_entities", "proper_noun_candidates"):
        for it in getattr(facts, bucket):
            key = (it.value.lower(), bucket)
            e = ents.get(key)
            if e is None:
                ents[key] = EntitySummary(text=it.value, type=bucket, count=1, first_start=it.start)
            else:
                e.count += 1
    # a proper-noun candidate that NER also found as a real entity is not listed twice
    named = {k[0] for k in ents if k[1] != "proper_noun_candidates"}
    entities = [e for k, e in ents.items() if not (k[1] == "proper_noun_candidates" and k[0] in named)]
    entities.sort(key=lambda e: (-e.count, e.first_start))

    return MeetingContext(
        duration_seconds=t.duration_seconds,
        segment_count=len(t.segments),
        word_count=sum(len(s.text.split()) for s in t.segments),
        speakers=sorted(spk.values(), key=lambda s: -s.talk_seconds),
        entities=entities,
        terms=[x.term for x in facts.acronyms] + [x.term for x in facts.technical_terms],
        topic_hints=topic_hints,
        notes=notes,
    )
