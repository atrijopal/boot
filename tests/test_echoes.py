"""Hints found by code for the refinement step: a phrase said again nearby and written differently."""
import app.core.pipeline as pl
from app.core import echoes
from app.core.pipeline import run_phase1, run_phase2
from app.core.units import build_units
from tests.support import install, make_transcript, uid


def units_of(*texts):
    return build_units(make_transcript([("A", t) for t in texts]))[0]


def test_a_phrase_said_again_and_written_differently_is_found():
    found = echoes.find(units_of("But for for the canal number... Channel numbers, you still need them."))
    assert [(a, b) for _, a, b in found] == [("canal number", "Channel numbers")]


def test_plurals_function_words_spelling_variants_and_everyday_word_swaps_are_not_echoes():
    for text in ("You can't keep it and you can't make it.", "the colors of the car and the color of the case", "if it is really thin, it is really thin",
                 "I think so and I thing so", "Do we need them or do we need them", "the push button and the push buttons"):
        assert echoes.find(units_of(text)) == [], text


def test_an_echo_never_spans_two_sentences():
    assert echoes.find(units_of("The canal. Number two canals are open.")) == []


def test_the_refiner_is_shown_the_echo_and_the_fix_is_still_gated(wav, monkeypatch):
    t = make_transcript([("A", "Welcome everyone."), ("B", "I think for the canal number buttons... Channel numbers, you still need them."), ("A", "Fine, next the case.")])
    fake = install(monkeypatch, transcript=t)
    u = uid(t, 1)
    fake.r_refine = lambda user: {"edits": [{"u": u, "find": "canal number", "rep": "channel number", "k": "term"}]}
    run_phase1(wav, job_id="e1")
    p2 = run_phase2("e1")
    user = next(c[3] for c in fake.calls if c[0] == "refine")
    assert f'REPEATED (said again nearby, written differently; one of them may be a mis-hearing): {u} "canal number" / "Channel numbers"' in user
    e = p2.edits[0]
    assert e.find == "canal number" and e.status in ("applied", "suggested") and e.replace == "channel number"
    assert "canal number" in pl.load_phase1("e1").transcript.segments[1].text  # the raw transcript is never touched


def test_a_meeting_with_no_echoes_sends_no_repeated_line(wav, monkeypatch):
    t = make_transcript([("A", "Welcome everyone."), ("B", "We keep the plain design."), ("A", "Fine, next the case.")])
    fake = install(monkeypatch, transcript=t)
    fake.r_refine = lambda user: {"edits": []}
    run_phase1(wav, job_id="e2")
    run_phase2("e2")
    assert "REPEATED" not in next(c[3] for c in fake.calls if c[0] == "refine")


def test_the_real_failure_a_phrase_that_crosses_a_sentence_is_not_an_echo():
    """From a live run: "the watches too" / "the watch. You" produced a hint, a wrong fix and a damaged line."""
    text = "Have gone through with the watches too. You can customize the watch. You can see how it is."
    assert echoes.find(units_of(text)) == []


def test_a_fix_may_not_add_sentence_punctuation(wav, monkeypatch):
    t = make_transcript([("A", "Welcome everyone."), ("B", "Have gone through with the watches too. You can customize the watch. You can see it."), ("A", "Fine.")])
    fake = install(monkeypatch, transcript=t)
    u = uid(t, 1)
    fake.r_refine = lambda user: {"edits": [{"u": u, "find": "the watches too", "rep": "the watch. You", "k": "term"}]}
    run_phase1(wav, job_id="e3")
    p2 = run_phase2("e3")
    assert p2.edits[0].status == "rejected" and "sentence punctuation" in p2.edits[0].reject_reason
    assert p2.refined.segments[1].text == t.segments[1].text  # the line is unchanged
