import pytest

from app.core.gates import apply_edits, gate_edits, ground_names, ground_record, locate, numbers_in, protected_spans, suspect_hints
from app.core.units import build_units, make_windows, segment_of
from tests.support import MEETING, make_transcript, uid


def setup():
    t = make_transcript()
    units, legend = build_units(t)
    return t, units, {u.uid: u for u in units}, legend


# ---------------------------------------------------------------- units
def test_short_same_speaker_lines_are_merged_into_one_unit_with_offsets():
    rows = [("SPEAKER_00", "Okay."), ("SPEAKER_00", "So we need a price."), ("SPEAKER_01", "Yes."), ("SPEAKER_00", "25 euro. Agreed?")]
    t = make_transcript(rows)
    units, legend = build_units(t)
    assert [u.text for u in units] == ["Okay. So we need a price.", "Yes.", "25 euro. Agreed?"] and legend == {"A": "SPEAKER_00", "B": "SPEAKER_01"}
    first = units[0]
    assert first.seg_idx == [0, 1] and first.text[first.offsets[1][0] : first.offsets[1][1]] == "So we need a price."
    assert segment_of(first, 0) == 0 and segment_of(first, len("Okay. ") + 2) == 1


def test_a_long_pause_or_a_speaker_change_starts_a_new_unit():
    t = make_transcript([("SPEAKER_00", "One."), ("SPEAKER_00", "Two.")])
    t.segments[1] = t.segments[1].model_copy(update={"start": 100.0, "end": 101.0})
    assert len(build_units(t)[0]) == 2


def test_unknown_speakers_get_the_question_mark_tag():
    t = make_transcript(known=False)
    units, legend = build_units(t)
    assert all(u.tag == "?" and u.speaker == "SPEAKER_UNKNOWN" for u in units) and legend == {}


def test_windows_cover_every_unit_once_with_read_only_context():
    t, units, _, _ = setup()
    ws = make_windows(units, window_chars=120, overlap=2)
    assert len(ws) > 1 and [u.uid for w in ws for u in w.target] == [u.uid for u in units]
    assert all(len(w.context) <= 2 for w in ws) and ws[0].context == [] and ws[1].context == ws[0].target[-2:]


def test_prompt_lines_are_compact():
    t, units, _, _ = setup()
    w = make_windows(units, 10000, 0)[0]
    assert w.render().startswith("TRANSCRIPT:\nu001 ") and "SPEAKER_0" not in w.render()  # tags, not long labels


# ---------------------------------------------------------------- record gate
def rec(raw, window_ids=None):
    t, units, by_id, _ = setup()
    target = set(window_ids or by_id)
    return ground_record(raw, units, by_id, target, t)


def test_a_valid_record_is_grounded_with_time_and_speaker():
    t, units, by_id, _ = setup()
    r, why = rec({"t": "decision", "s": "confirmed", "x": "The group agreed on 25 euro.", "q": [[uid(t, 1), "Agreed, let's go with 25 euro"]]})
    assert why is None and r.start == 10.0 and r.speakers == ["SPEAKER_01"] and r.evidence[0].segment_ids == ["seg_0002"]


@pytest.mark.parametrize("raw,why", [
    ({"t": "decision", "s": "confirmed", "x": "ok text", "q": [["u002", "we will go with 30 euro"]]}, "not in the cited line"),
    ({"t": "decision", "s": "confirmed", "x": "The group agreed on 30 euro.", "q": [["u002", "let's go with 25 euro"]]}, "introduces a number"),
    ({"t": "decision", "s": "decided", "x": "ok text", "q": [["u002", "Agreed"]]}, "not valid for decision"),
    ({"t": "idea", "s": "stated", "x": "ok text", "q": [["u002", "Agreed"]]}, "unknown kind"),
    ({"t": "decision", "s": "confirmed", "x": "ok text", "q": []}, "1-4 quotes"),
    ({"t": "decision", "s": "confirmed", "x": "ok text", "q": [["u999", "x"]]}, "unknown line"),
    ({"t": "decision", "s": "confirmed", "x": "ab", "q": [["u002", "Agreed"]]}, "oversized"),
    ("not a dict", "malformed"),
])
def test_record_rejections(raw, why):
    r, reason = rec(raw)
    assert r is None and why in reason, reason


def test_a_quote_cited_under_a_neighbouring_line_is_reanchored_not_invented():
    t, units, by_id, _ = setup()
    r, why = ground_record({"t": "decision", "s": "confirmed", "x": "Someone agreed.", "q": [[units[0].uid, "Agreed, let's go with 25 euro"]]}, units, by_id, set(by_id), t)
    assert why is None and r.evidence[0].uid == units[1].uid
    far, _ = ground_record({"t": "decision", "s": "confirmed", "x": "Something was said.", "q": [[units[0].uid, "PSNR went up"]]}, units, by_id, set(by_id), t)
    assert far is not None and far.evidence[0].uid == units[-1].uid  # many lines away, but verbatim and said in exactly one line: kept, anchored to where it was really said
    nowhere, _ = ground_record({"t": "decision", "s": "confirmed", "x": "Something was said.", "q": [[units[0].uid, "PSNR went down"]]}, units, by_id, set(by_id), t)
    assert nowhere is None


def test_no_quote_from_the_analysed_window_is_rejected():
    t, units, by_id, _ = setup()
    r, why = ground_record({"t": "decision", "s": "confirmed", "x": "Something was agreed.", "q": [[units[1].uid, "Agreed"]]}, units, by_id, {units[-1].uid}, t)
    assert r is None and "window" in why


def test_numbers_may_be_spoken_or_digits():
    assert 25.0 in numbers_in("twenty five percent") and 12.5 in numbers_in("$12.50") and numbers_in("one of the things") == set()


def action(**kw):
    t, units, by_id, _ = setup()
    raw = {"t": "action", "s": "committed", "x": "Send the report.", "q": [[uid(t, 2), "I'll send the budget report by Friday"]]}
    raw.update(kw)
    return ground_record(raw, units, by_id, set(by_id), t)[0], uid(t, 2)


def test_owner_and_deadline_rules():
    r, u = action(o="first", ou="u003")
    assert r.attrs["owner"]["basis"] == "first_person" and r.attrs["owner"]["speaker"] == "SPEAKER_02"
    r, _ = action(o="first", ou="u001")  # a line that is not cited
    assert r.attrs["owner"]["basis"] == "unspecified"
    r, _ = action(o="named", on="Jessie")  # name not in the quote
    assert r.attrs["owner"]["basis"] == "unspecified"
    assert action()[0].attrs["owner"]["text"] is None and action()[0].attrs["deadline"] is None  # never guessed
    assert action(d="by Friday")[0].attrs["deadline"] == "by Friday" and action(d="next Tuesday")[0].attrs["deadline"] is None


def test_a_role_in_the_quote_can_be_the_owner():
    t = make_transcript([("SPEAKER_00", "As the industrial designer you will work on the design.")])
    units, _ = build_units(t)
    r, why = ground_record({"t": "action", "s": "assigned", "x": "Work on the design.", "o": "named", "on": "the industrial designer",
                            "q": [[units[0].uid, "As the industrial designer you will work on the design"]]}, units, {u.uid: u for u in units}, {units[0].uid}, t)
    assert why is None and r.attrs["owner"]["text"] == "the industrial designer"


def test_facts_need_a_label_and_a_value_whose_numbers_are_in_the_quote():
    t, units, by_id, _ = setup()
    ok = {"t": "fact", "s": "stated", "x": "The price is 25 euro.", "l": "price", "v": "25 euro", "q": [[uid(t, 1), "let's go with 25 euro"]]}
    assert ground_record(ok, units, by_id, set(by_id), t)[0].attrs["value"] == "25 euro"
    assert ground_record({**ok, "v": "30 euro"}, units, by_id, set(by_id), t)[0] is None
    assert ground_record({**ok, "l": ""}, units, by_id, set(by_id), t)[0] is None


# ---------------------------------------------------------------- names
def test_only_verbatim_self_introductions_become_names():
    t, units, by_id, legend = setup()
    tag = next(k for k, v in legend.items() if v == "SPEAKER_01")
    good = {"t": tag, "n": "Alima", "u": uid(t, 6), "q": "my name is Alima"}
    out, _ = ground_names([good], by_id, legend)
    assert out[0].speaker == "SPEAKER_01" and out[0].name == "Alima"
    assert ground_names([{**good, "q": "my name is Alma"}], by_id, legend)[0] == []  # not verbatim
    wrong = next(k for k in legend if k != tag)
    assert ground_names([{**good, "t": wrong}], by_id, legend)[0] == []  # said by someone else
    assert ground_names([{**good, "n": "the budget"}], by_id, legend)[0] == []  # not a name
    assert len(ground_names([good, {**good, "t": wrong}], by_id, legend)[0]) == 1


# ---------------------------------------------------------------- edit gate
def edits(raw):
    t, units, by_id, _ = setup()
    return gate_edits(raw, by_id, t, protected_spans(t)), t


def test_a_supported_acronym_fix_is_applied_and_changes_only_that_segment():
    t = make_transcript()
    uidx = uid(t, 5)
    es, t = edits([{"u": uidx, "find": "pee ess en are", "rep": "PSNR", "k": "acronym"}])
    assert es[0].status == "applied" and es[0].segment_id == "seg_0006"
    refined = apply_edits(t, es, {"SPEAKER_01": "Alima"})
    assert "PSNR metric" in refined.segments[5].text and refined.segments[0].text == t.segments[0].text
    assert refined.segments[6].speaker_name == "Alima" and [s.segment_id for s in refined.segments] == [s.segment_id for s in t.segments]


@pytest.mark.parametrize("find,rep,why", [
    ("1250", "1280", "digits"),
    ("don't", "do", "protected"),
    ("this text is not there", "x", "verbatim"),
    ("Friday", "Monday", "protected"),
])
def test_edits_that_touch_protected_facts_or_invent_text_are_blocked(find, rep, why):
    t = make_transcript()
    target = next(i for i, (_, text) in enumerate(MEETING) if find in text) if find in " ".join(x for _, x in MEETING) else 4
    es, _ = edits([{"u": uid(t, target), "find": find, "rep": rep, "k": "term"}])
    assert es[0].status == "rejected" and why in es[0].reject_reason


def test_an_unsupported_fix_is_only_suggested_and_never_applied():
    rows = list(MEETING)
    rows[5] = ("SPEAKER_00", "We deploy with kube control tomorrow.")
    t = make_transcript(rows)
    es, t = edits_for(t, [{"u": uid(t, 5), "find": "kube control", "rep": "kubectl", "k": "term"}])
    assert es[0].status == "suggested" and apply_edits(t, es, {}).segments[5].text == t.segments[5].text


def edits_for(t, raw):
    units, _ = build_units(t)
    return gate_edits(raw, {u.uid: u for u in units}, t, protected_spans(t)), t


def test_an_edit_spanning_two_transcript_lines_is_blocked():
    rows = [("SPEAKER_00", "We use the quick"), ("SPEAKER_00", "brown fox daily.")]
    t = make_transcript(rows)
    es, _ = edits_for(t, [{"u": uid(t, 0), "find": "quick brown", "rep": "quik brown", "k": "term"}])
    assert es[0].status == "rejected" and "two transcript lines" in es[0].reject_reason


def test_rare_words_become_free_hints():
    t = make_transcript([("SPEAKER_00", "The flumbazor needs a restart and the budget is fine.")])
    units, _ = build_units(t)
    assert ("u001", "flumbazor") in suspect_hints(t, units)


def test_a_quote_straddling_two_neighbouring_lines_is_accepted_when_verbatim():
    rows = [("SPEAKER_00", "So according to the brief we are selling this remote control"), ("SPEAKER_01", "for 25 euro on an international scale.")]
    t = make_transcript(rows)
    units, _ = build_units(t)
    by_id = {u.uid: u for u in units}
    assert len(units) == 2  # different speakers: two lines
    raw = {"t": "fact", "s": "stated", "x": "The remote is sold for 25 euro.", "l": "price", "v": "25 euro", "q": [[units[0].uid, "selling this remote control for 25 euro"]]}
    r, why = ground_record(raw, units, by_id, set(by_id), t)
    assert why is None and r.evidence[0].segment_ids == ["seg_0001", "seg_0002"] and "control for 25 euro" in r.evidence[0].quote
    # wording that is not in the transcript at all is still refused, and so is a number that is not there
    assert ground_record({**raw, "q": [[units[0].uid, "selling this remote control for 30 euro"]]}, units, by_id, set(by_id), t)[0] is None
    assert ground_record({**raw, "x": "The remote is sold for 30 euro.", "v": "30 euro"}, units, by_id, set(by_id), t)[0] is None


def test_a_verbatim_quote_with_a_wrong_line_number_is_kept_only_if_it_is_unique_in_the_window():
    t, units, by_id, _ = setup()
    far = units[-1].uid  # the quote below is said in the first line
    ok = {"t": "proposal", "s": "proposed", "x": "Go with a selling price of 25 euro.", "q": [[far, "I think we should go with 25 euro"]]}
    rec, why = ground_record(ok, units, by_id, set(by_id), t)
    assert rec is not None and rec.evidence[0].uid == units[0].uid and why is None
    ambiguous = {"t": "proposal", "s": "proposed", "x": "Go with 25 euro.", "q": [[far, "go with 25 euro"]]}  # said twice
    assert ground_record(ambiguous, units, by_id, set(by_id), t) == (None, "a quote is not in the cited line")
    invented = {"t": "proposal", "s": "proposed", "x": "Go with 30 euro.", "q": [[far, "we should go with 30 euro"]]}
    assert ground_record(invented, units, by_id, set(by_id), t)[0] is None
    assert ground_record({**ok, "q": [[far, "I think we should go with 25 euro..."]]}, units, by_id, set(by_id), t)[0] is not None  # a trailing ellipsis does not matter


def test_a_quote_that_skips_words_with_an_ellipsis_is_kept_when_every_piece_is_verbatim():
    t, units, by_id, _ = setup()
    uid = units[0].uid  # "So we need to decide the selling price, and I think we should go with 25 euro."
    ok = {"t": "proposal", "s": "proposed", "x": "Go with 25 euro.", "q": [[uid, "I think we should ... go with 25 euro"]]}
    rec, why = ground_record(ok, units, by_id, set(by_id), t)
    assert rec is not None and why is None and [e.quote for e in rec.evidence] == ["I think we should", "go with 25 euro"]
    bad = {"t": "proposal", "s": "proposed", "x": "Go with 25 euro.", "q": [[uid, "I think we should ... go with 99 euro"]]}
    assert ground_record(bad, units, by_id, set(by_id), t) == (None, "a quote is not in the cited line")  # a piece that was not said sinks the quote
    tiny = {"t": "proposal", "s": "proposed", "x": "Go with 25 euro.", "q": [[uid, "I ... 25 euro"]]}  # one-word piece ignored, the usable piece still verbatim
    assert ground_record(tiny, units, by_id, set(by_id), t)[0] is not None


def test_an_ellipsis_may_not_skip_a_negation():
    from app.core.units import Unit

    t, units, by_id, _ = setup()
    u = units[-1]  # "PSNR went up, but we don't need the LPIPS number."
    assert ground_record({"t": "decision", "s": "confirmed", "x": "They need the LPIPS number.", "q": [[u.uid, "but we ... need the LPIPS number"]]}, units, by_id, set(by_id), t) == (None, "an ellipsis in a quote skips a negation")
    assert ground_record({"t": "decision", "s": "confirmed", "x": "PSNR went up.", "q": [[u.uid, "PSNR went ... but we"]]}, units, by_id, set(by_id), t)[0] is not None  # skipped words with no negation are fine
