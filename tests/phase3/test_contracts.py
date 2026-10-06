import numpy as np
import pytest

from app.schemas.phase3 import Event, SourceRef
from app.schemas.transcript import Word
from app.summarization.embeddings import embed_units
from app.summarization.importance import allocate_importance
from app.summarization.topics import group_topics
from app.summarization.units import build_units, source_revision
from app.summarization.validation import gate_event, ground_refs, numeric_fidelity
from tests.phase3.conftest import raw_event


def test_units_are_exact_source_spans_and_timing_is_conserved(source):
    source.refined_transcript.segments[0].text = "Budget is approved. Order the prototype."
    units = build_units(source)
    by_seg = {s.segment_id: s for s in source.refined_transcript.segments}
    for u in units:
        assert u.text == by_seg[u.segment_id].text[u.start_char:u.end_char]
        assert u.source_revision == source_revision(source)
    assert sum(u.duration_seconds for u in units if u.segment_id == "seg_0001") == pytest.approx(8)


def test_aligned_word_time_excludes_pauses_and_changed_text_falls_back(source):
    s = source.refined_transcript.segments[0]
    s.text = "Budget approved."
    s.words = [Word(text="Budget", start=120, end=121), Word(text="approved.", start=126, end=127)]
    u = build_units(source)[0]
    assert u.timing_precision == "word" and u.duration_seconds == 2
    s.text = "Budget rejected."
    u = build_units(source)[0]
    assert u.timing_precision == "segment_estimate" and u.duration_seconds == 8


def test_zero_duration_is_missing_not_measured_zero(source):
    s = source.refined_transcript.segments[0]
    s.end = s.start
    units = build_units(source)
    assert units[0].duration_seconds is None
    assert allocate_importance(units, [], [])["missing_duration_units"] == 1


def test_unknown_speaker_never_becomes_an_owner(source):
    source.refined_transcript.segments[6].speaker_known = False
    units = build_units(source)
    event = Event.model_validate(raw_event(7, "action", "committed", owner_speaker_id="S3", deadline_text="by next Friday"))
    event = gate_event(event, {u.unit_id:u for u in units}, {"u000007"})
    assert event.owner_speaker_id is None
    assert event.deadline_text == "by next Friday"


def test_invalid_evidence_and_numeric_changes_are_rejected(source):
    units = build_units(source)
    by_unit = {u.unit_id:u for u in units}
    for ref in [SourceRef(unit_id="missing", quote="x"), SourceRef(unit_id="u000001", quote="₹30,000")]:
        with pytest.raises(ValueError):
            ground_refs([ref], by_unit)
    event = Event.model_validate(raw_event(1, "proposal", "proposed"))
    event.description = "The prototype budget is ₹30,000."
    with pytest.raises(ValueError, match="numeric"):
        gate_event(event, by_unit, {"u000001"})


def test_field_specific_evidence_is_required(source):
    units = build_units(source)
    event = Event.model_validate(raw_event(7, "action", "committed"))
    event.owner_text = "Priya"
    event.deadline_text = "Friday"
    checked = gate_event(event, {u.unit_id:u for u in units}, {"u000007"})
    assert checked.owner_text is None and checked.deadline_text is None


def test_status_contract_rejects_decision_commitment():
    with pytest.raises(ValueError):
        Event.model_validate(raw_event(5, "decision", "committed"))


def test_revisit_reuses_topic_id_and_weights_sum_to_one(source):
    units = build_units(source)
    vectors = np.array([[0., 1.] if u.unit_id == "u000003" else [1., 0.] for u in units])
    topics = group_topics(units, vectors)
    assert len(topics) == 2
    assert units[0].memberships == units[3].memberships == units[-1].memberships
    assert units[0].memberships != units[2].memberships
    assert len(topics[0].occurrences) == 2
    assert all(sum(u.memberships.values()) + u.unresolved_weight == pytest.approx(1) for u in units)


def test_fractional_duration_is_never_counted_twice(source):
    units = build_units(source)
    vectors = np.array([[1., 0.] if i % 2 == 0 else [0., 1.] for i in range(len(units))])
    topics = group_topics(units, vectors)
    units[0].memberships = {topics[0].topic_id: .7, topics[1].topic_id: .3}
    accounting = allocate_importance(units, topics, [], 100)
    assert sum(t.duration_seconds for t in topics) + accounting["unresolved_seconds"] == pytest.approx(72)
    assert sum(t.narrative_words for t in topics) == 100


def test_filler_with_long_gap_remains_unresolved(source):
    units = build_units(source)
    units[5].start_seconds += 100
    vectors = np.array([[1., 0.]] * len(units))
    group_topics(units, vectors)
    assert units[5].unresolved_weight == 1


def test_empty_vector_does_not_force_a_relative_softmax_winner(source):
    units = build_units(source)
    topics = group_topics(units, np.zeros((len(units), 2)))
    assert topics == [] and all(u.unresolved_weight == 1 for u in units)


def test_existing_spacy_vector_backend_is_normalized(source):
    rows = embed_units(build_units(source), "spacy")
    assert rows.shape[0] == 9 and rows.shape[1] > 0
    assert np.allclose(np.linalg.norm(rows, axis=1), 1)


def test_source_revision_changes_for_accepted_names(source):
    revision = source_revision(source)
    source.refined_transcript.segments[0].speaker_name = "Priya"
    assert source_revision(source) != revision


def test_oversized_single_token_does_not_duplicate_word_time(source):
    s = source.refined_transcript.segments[0]
    s.text = "x" * 150
    s.words = [Word(text=s.text, start=s.start, end=s.end)]
    units = [u for u in build_units(source, max_chars=50) if u.segment_id == s.segment_id]
    assert sum(u.duration_seconds for u in units) == pytest.approx(8)


def test_supersession_cannot_point_forward_in_time():
    from app.summarization.relations import relation_allowed
    first = Event.model_validate(raw_event(5, "decision", "confirmed"))
    last = Event.model_validate(raw_event(9, "decision", "confirmed"))
    first.event_id, last.event_id = "old", "new"
    pos = {first.anchor_unit_id: 5, last.anchor_unit_id: 9}
    assert relation_allowed("SUPERSEDES", last, first, pos)
    assert not relation_allowed("SUPERSEDES", first, last, pos)


def test_spacy_groups_the_scripted_topic_return(source):
    units = build_units(source)
    topics = group_topics(units, embed_units(units, "spacy"))
    primary = lambda u: max(u.memberships, key=u.memberships.get)
    assert primary(units[0]) == primary(units[3]) == primary(units[8])
    assert primary(units[0]) != primary(units[2])

