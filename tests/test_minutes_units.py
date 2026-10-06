"""Pure-code parts of Phase 3: grounding gate, merge, topic ranges, number-mismatch check, reconciliation rules, rendering."""
import json

import pytest

from app.minutes.chunking import make_chunks
from app.minutes.figures import flag_number_mismatches
from app.minutes.grounding import ground_item, locate, numbers_in
from app.minutes.llm import Ctx
from app.minutes.merge import dedupe
from app.minutes.reconcile import reconcile
from app.minutes.render import actions_csv, registers, to_export, to_markdown
from app.minutes.topics import normalize_ranges
from app.minutes.trace import Tracer
from app.preprocessing.pipeline import extract_facts
from app.schemas.minutes import Evidence, Item, Minutes, RunMeta, Verification
from tests.minutes_support import ev, transcript


def setup_chunk():
    t = transcript()
    chunk = make_chunks(t, 45, 6)[0]
    return t, chunk, {s.segment_id: s for s in t.segments}


def ground(raw, allowed=("decision", "proposal", "disagreement")):
    _, chunk, by_id = setup_chunk()
    return ground_item(raw, allowed, chunk, by_id)


# ---------------------------------------------------------------- grounding
def test_locate_exact_and_tolerant():
    assert locate("We'll go with 25 euro.", "go with 25 euro") == (6, 21)
    assert locate("It’s fine  now", "It's fine now") == (0, 14)  # curly quote + double space
    assert locate("one two three", "two four") is None  # wording is never tolerated


def test_valid_decision_is_grounded_with_speaker_and_times():
    item, why = ground({"lens": "decision", "text": "The group agreed on 25 euro.", "status": "confirmed", "evidence": [ev(2, "let's go with 25 euro")]})
    assert why is None and item.start == 10.0 and item.speakers == ["Alima"] and item.evidence[0].speaker == "SPEAKER_01"
    assert item.evidence[0].quote == "let's go with 25 euro"


@pytest.mark.parametrize("raw,why", [
    ({"lens": "decision", "text": "ok text", "status": "confirmed", "evidence": [ev(2, "we will go with 30 euro")]}, "not in the cited segment"),
    ({"lens": "decision", "text": "The group agreed on 30 euro.", "status": "confirmed", "evidence": [ev(2, "let's go with 25 euro")]}, "introduces a number"),
    ({"lens": "decision", "text": "ok text", "status": "decided", "evidence": [ev(2, "let's go with 25 euro")]}, "not valid for decision"),
    ({"lens": "action", "text": "ok text", "status": "confirmed", "evidence": [ev(2, "Agreed")]}, "not allowed here"),
    ({"lens": "decision", "text": "ok text", "status": "confirmed", "evidence": []}, "1-4 evidence"),
    ({"lens": "decision", "text": "ok text", "status": "confirmed", "evidence": [{"segment_id": "seg_9999", "quote": "x"}]}, "unknown segment"),
    ({"lens": "decision", "text": "ab", "status": "confirmed", "evidence": [ev(2, "Agreed")]}, "empty or oversized"),
    ("not a dict", "malformed"),
])
def test_grounding_rejections(raw, why):
    item, reason = ground(raw)
    assert item is None and why in reason, reason


def test_spoken_numbers_match_digit_forms():
    assert 25.0 in numbers_in("twenty five percent") and 12.5 in numbers_in("$12.50") and numbers_in("one of the things") == set()
    t = transcript([("SPEAKER_00", "Rose", "We aim for twenty five euro per unit.")])
    chunk = make_chunks(t, 45, 6)[0]
    item, why = ground_item({"lens": "fact", "label": "price", "value": "25 euro", "text": "Price is 25 euro.", "status": "stated",
                             "evidence": [{"segment_id": "seg_0001", "quote": "twenty five euro per unit"}]}, ("fact",), chunk, {s.segment_id: s for s in t.segments})
    assert why is None and item.attrs["value"] == "25 euro"


def test_no_quote_from_target_segments_is_rejected():
    t = transcript()
    chunk = make_chunks(t, 5, 3)[1]  # target = seg 6..10, context before = seg 3..5
    item, why = ground_item({"lens": "decision", "text": "Something was agreed.", "status": "confirmed", "evidence": [ev(4, "Jessie, can you check the supplier list")]},
                            ("decision",), chunk, {s.segment_id: s for s in t.segments})
    assert item is None and "target" in why


def action(**kw):
    raw = {"lens": "action", "text": "Send the report.", "status": "committed", "owner_basis": None, "owner_segment_id": None, "owner_text": None,
           "deadline_text": None, "evidence": [ev(3, "I'll send the budget report by Friday")]}
    raw.update(kw)
    return ground(raw, ("action",))


def test_owner_rules():
    it, _ = action(owner_basis="first_person", owner_segment_id="seg_0003")
    assert it.attrs["owner"] == {"text": "Ian", "basis": "first_person", "speaker": "SPEAKER_02"}
    it, _ = action(owner_basis="first_person", owner_segment_id="seg_0001")  # a segment that is not cited
    assert it.attrs["owner"]["basis"] == "unspecified"
    it, _ = action(owner_basis="named_in_quote", owner_text="Jessie")  # name not in the quote
    assert it.attrs["owner"]["basis"] == "unspecified"
    it, _ = action(evidence=[ev(4, "Jessie, can you check the supplier list")], owner_basis="named_in_quote", owner_text="Jessie")
    assert it.attrs["owner"]["text"] == "Jessie" and it.attrs["owner"]["basis"] == "named_in_quote"
    it, _ = action()
    assert it.attrs["owner"]["text"] is None and it.attrs["deadline"] is None  # never guessed


def test_deadline_must_be_in_the_quote():
    assert action(deadline_text="by Friday")[0].attrs["deadline"] == "by Friday"
    assert action(deadline_text="next Tuesday")[0].attrs["deadline"] is None


def test_first_person_needs_a_labelled_speaker():
    t = transcript([("SPEAKER_UNKNOWN", None, "I'll send it tomorrow.")])
    t.segments[0].speaker_known = False
    chunk = make_chunks(t, 45, 6)[0]
    it, _ = ground_item({"lens": "action", "text": "Send it.", "status": "committed", "owner_basis": "first_person", "owner_segment_id": "seg_0001",
                         "evidence": [{"segment_id": "seg_0001", "quote": "I'll send it tomorrow"}]}, ("action",), chunk, {"seg_0001": t.segments[0]})
    assert it.attrs["owner"]["basis"] == "unspecified"


# ---------------------------------------------------------------- merge
def item(lens, text, seg, quote, start, **attrs):
    e = Evidence(segment_id=f"seg_{seg:04d}", quote=quote, start=start, end=start + 5, speaker="SPEAKER_00", speaker_name="Rose", char_start=0, char_end=len(quote))
    return Item(lens=lens, text=text, status="confirmed" if lens == "decision" else "stated", evidence=[e], start=start, chunk_ids=["c01"], attrs=attrs)


def test_duplicates_from_overlapping_chunks_are_merged_with_all_evidence():
    a = item("decision", "The group agreed to a price of 25 euro.", 2, "go with 25 euro", 10)
    b = item("decision", "The group agreed on a price of 25 euro", 2, "Agreed", 10)
    b.chunk_ids = ["c02"]
    out = dedupe([a, b])
    assert len(out) == 1 and len(out[0].evidence) == 2 and out[0].chunk_ids == ["c01", "c02"]


def test_different_facts_in_one_segment_are_not_merged():
    a = item("fact", "Price is 25 euro.", 2, "25 euro", 10, label="price", value="25 euro", flags=[])
    b = item("fact", "Target is 50 million euro.", 2, "50 million euro", 10, label="profit target", value="50 million euro", flags=[])
    assert len(dedupe([a, b])) == 2


def test_distinct_decisions_stay_distinct_and_owner_survives_merge():
    d1 = item("decision", "Use a red case.", 2, "red case", 10)
    d2 = item("decision", "Ship in March.", 5, "March", 50)
    assert len(dedupe([d1, d2])) == 2
    a1 = item("action", "Send the report.", 3, "send it", 20, owner={"text": None, "basis": "unspecified", "speaker": None}, deadline=None)
    a2 = item("action", "Send the report.", 3, "send it", 20, owner={"text": "Ian", "basis": "first_person", "speaker": "S"}, deadline="Friday")
    merged = dedupe([a1, a2])
    assert len(merged) == 1 and merged[0].attrs["owner"]["text"] == "Ian" and merged[0].attrs["deadline"] == "Friday"


# ---------------------------------------------------------------- topics
def test_topic_ranges_are_made_contiguous_and_bounded():
    r = normalize_ranges([("A", 0, 4), ("B", 6, 9), ("C", 8, 15)], 20, 12)
    assert r[0][1] == 0 and r[-1][2] == 19
    assert all(r[i][2] + 1 == r[i + 1][1] for i in range(len(r) - 1))  # no gaps, no overlaps
    assert normalize_ranges([], 8, 5) == [("Full discussion", 0, 7)]
    assert normalize_ranges([("junk", 5, 2), ("x", 0, 99)], 8, 5) == [("Full discussion", 0, 7)]  # invalid spans ignored
    many = normalize_ranges([(f"T{i}", i * 5, i * 5 + 4) for i in range(10)], 50, 4)
    assert len(many) == 4 and many[-1][2] == 49
    tiny = normalize_ranges([("A", 0, 9), ("tiny", 10, 10), ("B", 11, 19)], 20, 12)
    assert [t[0] for t in tiny] == ["A", "B"]


# ---------------------------------------------------------------- number mismatch
def facts_for(*texts):
    return extract_facts(transcript([("SPEAKER_00", "Rose", t) for t in texts]).model_copy(update={"kind": "normalized"}))[0]


def fact(value, start):
    return item("fact", f"Value {value}", 1, value, start, label="cost", value=value, flags=[])


def test_misplaced_decimal_is_flagged_but_ordinary_differences_are_not():
    f = facts_for("It costs 1250 euro.", "No, it is $12.50.")
    it = fact("1250 euro", 0)
    it.item_id = "fct_001"
    warns = flag_number_mismatches([it], f)
    assert warns and "misplaced decimal" in it.attrs["flags"][0] and "12.50" in it.attrs["flags"][0]
    f2 = facts_for("We have 25 people.", "We need 250 chairs.")
    it2 = fact("25 people", 0)
    it2.item_id = "fct_002"
    assert flag_number_mismatches([it2], f2) == []  # two digits only: too common to mean anything
    f3 = facts_for("It costs 1250 euro.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "Fine.", "It is $12.50.")
    it3 = fact("1250 euro", 0)
    it3.item_id = "fct_003"
    assert flag_number_mismatches([it3], f3) == []  # 200 s apart: not related


# ---------------------------------------------------------------- reconcile validation
def rctx():
    t = transcript()
    return Ctx(job_id="x", tracer=Tracer(), transcript=t)


def rel_items():
    dec = item("decision", "Go with 25 euro.", 2, "let's go with 25 euro", 10)
    old = item("decision", "Go with 30 euro.", 1, "go with 25 euro", 0)
    prp = item("proposal", "Use 25 euro.", 1, "I think we should go with 25 euro", 0)
    prp.status = "proposed"
    q = item("question", "Is the cost 1250 euro?", 5, "is it 1250 euro?", 40)
    q.status = "open"
    for n, i in enumerate([old, prp, dec, q]):
        i.item_id = ["dec_001", "prp_001", "dec_002", "qst_001"][n]
    return old, prp, dec, q


def run_reconcile(monkeypatch, relations):
    import app.minutes.reconcile as r
    monkeypatch.setattr(r, "ask", lambda *a, **k: {"relations": relations})
    items = list(rel_items())
    return items, *r.reconcile(rctx(), items)


def test_valid_relations_are_applied(monkeypatch):
    items, rels, warns = run_reconcile(monkeypatch, [
        {"source": "dec_002", "target": "dec_001", "kind": "supersedes", "segment_id": "seg_0002", "quote": "let's go with 25 euro"},
        {"source": "dec_002", "target": "prp_001", "kind": "adopts", "segment_id": "seg_0002", "quote": "let's go with 25 euro"}])
    old, prp, dec, q = items
    assert len(rels) == 2 and old.status == "superseded" and old.superseded_by == "dec_002" and prp.status == "accepted" and "dec_002" in old.related


@pytest.mark.parametrize("rel", [
    {"source": "dec_001", "target": "dec_002", "kind": "supersedes", "segment_id": "seg_0002", "quote": "let's go with 25 euro"},  # earlier cannot replace later
    {"source": "dec_002", "target": "dec_001", "kind": "supersedes", "segment_id": "seg_0002", "quote": "we will drop the old one"},  # quote not verbatim
    {"source": "dec_002", "target": "dec_001", "kind": "supersedes", "segment_id": "seg_0009", "quote": "25 euro is fine"},  # segment not in either item's evidence
    {"source": "dec_002", "target": "qst_001", "kind": "adopts", "segment_id": "seg_0002", "quote": "let's go with 25 euro"},  # adopts needs a proposal
    {"source": "dec_002", "target": "dec_002", "kind": "contradicts", "segment_id": "seg_0002", "quote": "let's go with 25 euro"},  # self relation
    {"source": "nope", "target": "dec_001", "kind": "supersedes", "segment_id": "seg_0002", "quote": "go with"},  # unknown id
    {"source": "dec_002", "target": "dec_001", "kind": "replaces", "segment_id": "seg_0002", "quote": "go with"},  # unknown kind
])
def test_invalid_relations_are_ignored(monkeypatch, rel):
    items, rels, _ = run_reconcile(monkeypatch, [rel])
    assert rels == [] and all(i.status in ("confirmed", "proposed", "open", "stated") for i in items)


def test_contradiction_is_reported_not_resolved(monkeypatch):
    items, rels, warns = run_reconcile(monkeypatch, [{"source": "dec_002", "target": "dec_001", "kind": "contradicts", "segment_id": "seg_0002", "quote": "let's go with 25 euro"}])
    assert len(rels) == 1 and any("Conflicting decisions" in w for w in warns) and items[0].status == "confirmed"


# ---------------------------------------------------------------- rendering
def make_minutes(items=None, needs_review=None, status="complete"):
    return Minutes(meta=RunMeta(job_id="j", run_id="r1", source_revision="rev", created_at="now", duration_seconds=100, segments=10, chunks=1, models={}),
                   status=status, items=items or [], needs_review=needs_review or [], warnings=["a limitation"])


def test_empty_registers_say_so_and_unspecified_is_shown():
    md = to_markdown(make_minutes())
    assert md.count("None recorded in this meeting.") == 7 and "No verified outcomes were extracted" in md
    a = item("action", "Send the report.", 3, "send it", 20, owner={"text": None, "basis": "unspecified", "speaker": None}, deadline=None)
    a.item_id, a.verification = "act_001", Verification(status="verified")
    md = to_markdown(make_minutes([a]))
    assert "Owner: unspecified | Deadline: unspecified" in md
    assert "unspecified" in actions_csv(make_minutes([a]))


def test_markdown_and_json_carry_the_same_items_and_no_html_escapes():
    d = item("decision", "The group agreed it's fine to use 25 euro.", 2, "let's go with 25 euro", 10)
    d.item_id = "dec_001"
    a = item("action", "Send it.", 3, "send it", 20, owner={"text": "Ian", "basis": "first_person", "speaker": "S"}, deadline="Friday")
    a.item_id = "act_001"
    m = make_minutes([d, a])
    md, js = to_markdown(m), to_export(m)
    ids_json = {i["item_id"] for reg in js["registers"].values() for i in reg}
    assert ids_json == {"dec_001", "act_001"} and all(i in md for i in ids_json)
    assert "&#x27;" not in md and "it's fine" in md and js["schema_version"] == "minutes/1"
    assert js["registers"]["action_items"][0]["attrs"]["owner"]["text"] == "Ian" and "Owner: Ian" in md
    assert [i.item_id for i in registers(m)["decisions"]] == ["dec_001"]
    json.dumps(js)  # serialisable


def test_partial_runs_are_marked_and_review_items_are_listed():
    r = item("decision", "Maybe go with 25 euro.", 2, "let's go with 25 euro", 10)
    r.item_id, r.verification = "rev_001", Verification(notes=["status label not supported: it was only a suggestion"])
    md = to_markdown(make_minutes(needs_review=[r], status="partial"))
    assert "INCOMPLETE" in md and "Items needing review" in md and "only a suggestion" in md


# ---------------------------------------------------------------- verifier contract (regression: live run rejected valid facts/risks/questions)
def test_every_status_has_a_plain_meaning():
    from app.minutes.lenses import DERIVED_STATUS, LENSES, STATUS_MEANING
    for lens, spec in LENSES.items():
        for st in spec["statuses"]:
            assert STATUS_MEANING.get((lens, st)), f"no meaning for {lens}/{st}"
    assert not (set(DERIVED_STATUS) - set(LENSES))


def test_verifier_records_carry_the_status_meaning():
    from app.minutes.verify import _record
    t = transcript()
    ctx = Ctx(job_id="x", tracer=Tracer(), transcript=t)
    index = {s.segment_id: i for i, s in enumerate(t.segments)}
    f = item("fact", "The selling price is 25 euro.", 2, "let's go with 25 euro", 10, label="price", value="25 euro", flags=[])
    f.item_id = "tmp_1"
    rec = _record(ctx, f, index)
    assert rec["kind_of_item"] == "fact" and rec["status"] == "stated" and "stated as a fact" in rec["status_meaning"]
    assert rec["context"] and rec["label"] == "price"


def test_quote_cited_under_a_neighbouring_segment_is_reanchored_not_invented():
    t = transcript()
    chunk = make_chunks(t, 45, 6)[0]
    by_id = {s.segment_id: s for s in t.segments}
    # the quote really is in seg_0002 but the model cited seg_0001
    item, why = ground_item({"lens": "decision", "text": "The group agreed on 25 euro.", "status": "confirmed",
                             "evidence": [ev(1, "Agreed, let's go with 25 euro")]}, ("decision",), chunk, by_id)
    assert why is None and item.evidence[0].segment_id == "seg_0002" and item.evidence[0].speaker_name == "Alima"
    assert by_id["seg_0002"].text[item.evidence[0].char_start : item.evidence[0].char_end] == item.evidence[0].quote
    # a quote that is nowhere nearby is still rejected, and so is one that only exists far away
    assert ground_item({"lens": "decision", "text": "Something was agreed.", "status": "confirmed", "evidence": [ev(1, "we will drop the old one")]},
                       ("decision",), chunk, by_id)[0] is None
    far, _ = ground_item({"lens": "decision", "text": "Someone disagreed.", "status": "confirmed", "evidence": [ev(1, "I disagree, 25 euro is fine")]},
                         ("decision",), chunk, by_id)
    assert far is None  # seg_0009 is 8 segments away from the cited seg_0001
