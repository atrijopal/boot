import pytest

from app.preprocessing.numbers import extract_numbers
from app.preprocessing.datetimes import extract_dates, extract_durations, extract_times
from app.preprocessing.pipeline import extract_facts
from app.schemas.transcript import Transcript, TranscriptSegment, Word


def seg(text, i=1, speaker="SPEAKER_UNKNOWN", known=False, **kw):
    return TranscriptSegment(segment_id=f"seg_{i:04d}", start=i * 10.0, end=i * 10.0 + 9, text=text, speaker=speaker, speaker_known=known, **kw)


def facts_for(*texts, **kw):
    t = Transcript(provider="t", model="t", kind="normalized", duration_seconds=len(texts) * 10.0, segments=[seg(x, i + 1, **kw) for i, x in enumerate(texts)])
    return extract_facts(t)[0]


@pytest.mark.parametrize(
    "text,value,surface",
    [
        ("We don't need 81.73% accuracy.", "81.73", "81.73%"),
        ("It is 2x faster.", "2", "2x"),
        ("The budget is $200.", "$200", "$200"),
        ("Ship v2.1 first.", "v2.1", "v2.1"),
        ("Use 10-15 samples.", "10-15", "10-15"),
        ("About 1,200 users.", "1200", "1,200"),
        ("We got eighty one point seven three percent.", "81.73", "eighty one point seven three percent"),
        ("That is one hundred and twenty three items.", "123", "one hundred and twenty three"),
    ],
)
def test_numbers_preserved(text, value, surface):
    got = [(n.value, n.surface) for n in extract_numbers(seg(text))]
    assert (value, surface) in got


def test_lone_one_is_not_a_number():
    assert extract_numbers(seg("One of the things we saw.")) == []


def test_number_provenance():
    n = extract_numbers(seg("Accuracy was 81.73% here.", i=3))[0]
    assert n.segment_id == "seg_0003"
    assert (n.start, n.end) == (30.0, 39.0)
    assert "Accuracy was 81.73% here."[n.char_start : n.char_end] == "81.73%"


def test_word_timestamps_narrow_the_span():
    s = seg("Accuracy was 81.73% here.", i=1, words=[Word(text="Accuracy", start=10, end=10.5), Word(text="was", start=10.5, end=10.7), Word(text="81.73%", start=10.7, end=11.6), Word(text="here.", start=11.6, end=12)])
    n = extract_numbers(s)[0]
    assert (n.start, n.end) == (10.7, 11.6)


def test_dates_times_not_resolved():
    s = seg("Let's meet Friday at 3 PM, or next Monday around noon, by end of day, within two weeks, on 7 October 2026.")
    dates = {d.surface for d in extract_dates(s)}
    assert {"Friday", "next Monday", "end of day", "7 October 2026"} <= dates
    assert {t.surface for t in extract_times(s)} >= {"3 PM", "around noon"}
    assert any("two weeks" in d.surface for d in extract_durations(s))
    assert all(d.extra["resolved"] is False for d in extract_dates(s))


def test_modal_may_is_not_a_month():
    assert extract_dates(seg("We may go there.")) == []


def test_times_and_dates_are_not_double_reported_as_numbers():
    f = facts_for("Let's meet at 15:30 on 2026-10-07.")
    assert f.numbers == []
    assert f.times and f.dates


def test_negations_with_scope_and_emphasis():
    f = facts_for("I don't agree.", "We should NOT deploy this.", "We don't need 81.73 percent accuracy.")
    cues = {(n.segment_id, n.surface) for n in f.negations}
    assert ("seg_0001", "don't") in cues and ("seg_0002", "NOT") in cues and ("seg_0003", "don't") in cues
    not_item = next(n for n in f.negations if n.surface == "NOT")
    assert not_item.extra["emphasis"] is True and "deploy" in not_item.extra["scope"]
    assert "81.73" in next(n for n in f.negations if n.segment_id == "seg_0003").extra["scope"]


def test_parser_finds_negation_without_any_word_list():
    f = facts_for("Nobody wants it, and we never shipped it.", "We cannot do that.")
    assert {n.surface.lower() for n in f.negations} >= {"never", "cannot"}


def test_person_found_and_no_speaker_invented():
    f = facts_for("Rahul will send the report on Friday.")
    assert any("Rahul" in p.value for p in f.people + f.proper_noun_candidates)


def test_unfinished_and_empty_sentences_do_not_crash():
    f = facts_for("So the thing is that we", "...", "Um, yeah, and then")
    assert f is not None


@pytest.mark.parametrize(
    "texts,expected",
    [
        (["The NAFNet baseline reached a PSNR gain.", "We tune the LPIPS loss and PSNR again.", "NAFNet beats the baseline on PSNR."], {"NAFNet", "PSNR"}),
        (["The plaintiff filed an interlocutory appeal.", "The interlocutory appeal was denied; the plaintiff will refile.", "Our counsel drafts the interlocutory brief."], {"interlocutory"}),
        (["Start the sourdough with more starter.", "The sourdough needs proofing overnight.", "Check the proofing and the sourdough crumb."], {"sourdough", "proofing"}),
        (["The patient had tachycardia on admission.", "We monitor the tachycardia and give metoprolol.", "Metoprolol dosing for tachycardia is adjusted."], {"tachycardia"}),
    ],
)
def test_terms_discovered_without_any_domain_config(texts, expected):
    f = facts_for(*texts)
    found = {t.term.lower() for t in f.technical_terms + f.acronyms}
    assert {e.lower() for e in expected} <= found


def test_emphasis_caps_are_not_acronyms():
    f = facts_for("We should NOT deploy this.")
    assert "NOT" not in {a.term for a in f.acronyms}


def test_asr_suspect_flag_from_low_confidence():
    s = seg("The flumbazor needs a restart.", avg_logprob=-1.4)
    t = Transcript(provider="t", model="t", kind="normalized", duration_seconds=10, segments=[s])
    f = extract_facts(t)[0]
    suspects = [x for x in f.technical_terms if x.asr_suspect]
    assert any(x.term == "flumbazor" for x in suspects)
