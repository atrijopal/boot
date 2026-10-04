"""Gatekeeper + apply logic. Pure code: no LLM, no network."""
import pytest

from app.refinement.apply import invariant_report, is_applied, render_refined
from app.refinement.validate import Support, check_edit, protected_spans
from app.preprocessing.pipeline import extract_facts
from app.schemas.refinement import Edit
from app.schemas.transcript import Transcript, TranscriptSegment


def make(*texts):
    segs = [TranscriptSegment(segment_id=f"seg_{i + 1:04d}", start=i * 10.0, end=i * 10.0 + 9, text=t, speaker=f"SPEAKER_0{i % 2}", speaker_known=True)
            for i, t in enumerate(texts)]
    return Transcript(provider="t", model="t", kind="normalized", duration_seconds=len(texts) * 10.0, segments=segs)


def gate(texts, seg_idx, find, replace, kind="term", second=None, taken=(), **kw):
    t = make(*texts)
    facts, _ = extract_facts(t)
    seg = t.segments[seg_idx]
    sup = Support(transcript=t, phase1_terms=set(), second_text={seg.segment_id: second} if second else {})
    return check_edit(seg, {"find": find, "replace": replace, "kind": kind}, sup, protected_spans(facts, seg.segment_id), list(taken), 0, kw.get("n", 0))


def test_supported_and_similar_is_applied():
    st, why, support, sim, span = gate(["We track pee ess en are on the test set.", "PSNR went up a bit."], 0, "pee ess en are", "PSNR", "acronym")
    assert st == "applied" and why is None and span == (9, 23)
    assert {e.kind for e in support} >= {"elsewhere", "similarity"}


def test_similar_but_no_evidence_is_only_suggested():
    st, _, support, sim, _ = gate(["We deploy with kube control tomorrow."], 0, "kube control", "kubectl")
    assert st == "suggested" and sim > 0.5 and not any(e.kind in ("elsewhere", "second_asr", "phase1_term") for e in support)


def test_second_asr_agreement_applies_even_if_not_similar():
    st, _, support, _, _ = gate(["Please ask Jessie about it."], 0, "Jessie", "Jesse", "name", second="please ask Jesse about it")
    assert st == "applied" and any(e.kind == "second_asr" for e in support)


def test_evidence_without_similarity_is_not_applied():
    # the replacement exists elsewhere, but is nothing like the original: a guess, not a correction
    st, *_ = gate(["We saw the whale.", "The turtle was big."], 0, "whale", "turtle")
    assert st == "suggested"


@pytest.mark.parametrize(
    "texts,find,replace,kind,expect",
    [
        (["We hit 81.73 percent."], "81.73", "81.78", "term", "protected"),
        (["Meet on Friday please."], "Friday", "Monday", "name", "protected"),
        (["We don't need it."], "don't", "do", "term", "protected"),
        (["We have a tree here."], "tree", "three", "spelling", "numbers changed"),
        (["We have forty here."], "forty", "fourty", "spelling", "protected"),
        (["It is a good plan."], "good plan", "great plan 2", "term", "digits"),
        (["It is a good plan."], "missing text", "x", "term", "not found verbatim"),
        (["go go go"], "go", "run", "term", "ambiguous"),
        (["It is a good plan."], "good", "better", "rewrite", "not allowed"),
        (["It is a good plan."], "good", "", "term", "deleting"),
        (["It is a good plan."], "good", "good", "term", "no change"),
        (["It is a good plan."], "good plan", "Good-Plan", "format", None),
        (["It is a good plan."], "good", "bad", "format", "may not change letters"),
        (["It is a plan."], "plan", "plan " + "very " * 20, "term", "longer"),
        (["a b c d e f g h i j k"], "a b c d e f g h i", "x", "term", "8 words"),
    ],
)
def test_rejections(texts, find, replace, kind, expect):
    st, why, *_ = gate(texts, 0, find, replace, kind)
    if expect is None:
        assert st != "rejected"
    else:
        assert st == "rejected" and expect in why, (st, why)


def test_overlapping_edit_in_same_segment_rejected():
    st, why, *_ = gate(["We use the quick brown fox."], 0, "quick brown", "quik brown", taken=[(11, 22)])
    assert st == "rejected" and "overlaps another edit" in why


def test_too_many_edits_rejected():
    st, why, *_ = gate(["We use the quick brown fox."], 0, "quick", "quik", n=6)
    assert st == "rejected" and "too many" in why


def edit(i, seg, find, replace, cs, status="applied", **kw):
    return Edit(edit_id=f"edt_{i:03d}", segment_id=seg, start=0, end=1, kind="term", find=find, replace=replace,
                char_start=cs, char_end=cs + len(find), status=status, **kw)


def test_render_applies_only_applied_and_keeps_structure():
    t = make("We use pee ess en are and kube control.", "Second segment stays.")
    edits = [edit(1, "seg_0001", "pee ess en are", "PSNR", 7), edit(2, "seg_0001", "kube control", "kubectl", 26, status="suggested")]
    r = render_refined(t, edits)
    assert r.segments[0].text == "We use PSNR and kube control."
    assert [(s.segment_id, s.start, s.end, s.speaker) for s in r.segments] == [(s.segment_id, s.start, s.end, s.speaker) for s in t.segments]
    assert t.segments[0].text.startswith("We use pee")  # input not mutated


def test_user_decisions_override_but_rule_rejected_never_applies():
    t = make("We use pee ess en are and kube control.")
    a = edit(1, "seg_0001", "pee ess en are", "PSNR", 7, user_decision="reject")
    s = edit(2, "seg_0001", "kube control", "kubectl", 26, status="suggested", user_decision="accept")
    x = edit(3, "seg_0001", "We", "They", 0, status="rejected", user_decision="accept", reject_reason="x")
    assert (is_applied(a), is_applied(s), is_applied(x)) == (False, True, False)
    assert render_refined(t, [a, s, x]).segments[0].text == "We use pee ess en are and kubectl."


def test_stale_or_overlapping_edits_are_skipped_not_corrupting():
    t = make("alpha beta gamma")
    r = render_refined(t, [edit(1, "seg_0001", "alpha beta", "A B", 0), edit(2, "seg_0001", "beta gamma", "B G", 6), edit(3, "seg_0001", "zzz", "y", 0)])
    assert r.segments[0].text in ("A B gamma", "alpha B G")


def test_invariant_report_ok_and_detects_tampering():
    t = make("We hit 81.73 percent and don't stop.")
    ok = invariant_report(t, render_refined(t, []))
    assert ok.all_ok
    bad = t.model_copy(update={"segments": [t.segments[0].model_copy(update={"text": "We hit 81.78 percent and do stop."})]})
    rep = invariant_report(t, bad)
    assert not rep.numbers_unchanged and not rep.negations_unchanged and not rep.all_ok
