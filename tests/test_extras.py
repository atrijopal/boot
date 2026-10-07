"""Names said to a person, accept/reject of fixes, typed speaker names, per-speaker summary, tasks by owner, timeline, email, calendar, report links."""
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.config.settings import settings
from app.core import corrections, insights
from app.core.gates import ground_names
from app.core.pipeline import load_phase2, load_result, run_phase1, run_phase2, run_phase3, run_pipeline
from app.core.units import build_units
from app.utils.errors import PipelineError
from tests.support import install, make_transcript


def units_of(rows):
    t = make_transcript(rows)
    units, legend = build_units(t)
    return units, {u.uid: u for u in units}, legend


ADDRESS = [("SPEAKER_00", "Amber, are you ready for the presentation?"), ("SPEAKER_01", "Yes I am, thanks."), ("SPEAKER_00", "Great, go ahead.")]


# ---------------------------------------------------------------------------------------------- names said to a person
def test_a_name_said_to_someone_is_kept_when_that_speaker_answers():
    units, by_id, legend = units_of(ADDRESS)
    tag_b = next(t for t, sp in legend.items() if sp == "SPEAKER_01")
    out, _ = ground_names([{"t": tag_b, "n": "Amber", "u": "u001", "q": "Amber, are you ready", "how": "addressed"}], by_id, legend)
    assert [(n.name, n.speaker, n.basis) for n in out] == [("Amber", "SPEAKER_01", "addressed")]


def test_a_name_said_to_someone_is_refused_without_a_reply_or_for_the_wrong_person():
    units, by_id, legend = units_of(ADDRESS)
    tag_a = next(t for t, sp in legend.items() if sp == "SPEAKER_00")
    tag_b = next(t for t, sp in legend.items() if sp == "SPEAKER_01")
    bad = [{"t": tag_a, "n": "Amber", "u": "u001", "q": "Amber, are you ready", "how": "addressed"},  # said by that same speaker
           {"t": tag_b, "n": "Amber", "u": "u001", "q": "Dana, are you ready", "how": "addressed"},  # quote not in the line
           {"t": tag_b, "n": "Zed", "u": "u001", "q": "Amber, are you ready", "how": "addressed"},  # name not in the quote
           {"t": tag_b, "n": "Amber", "u": "u003", "q": "Great, go ahead", "how": "addressed"}]  # no reply after it
    assert ground_names(bad, by_id, legend)[0] == []
    only_a = units_of([("SPEAKER_00", "Amber, are you ready?"), ("SPEAKER_02", "Hello?"), ("SPEAKER_00", "Anyone?"), ("SPEAKER_02", "Hmm."), ("SPEAKER_01", "Yes.")])
    tag_b2 = next(t for t, sp in only_a[2].items() if sp == "SPEAKER_01")
    assert ground_names([{"t": tag_b2, "n": "Amber", "u": "u001", "q": "Amber, are you ready", "how": "addressed"}], only_a[1], only_a[2])[0] == []  # reply too far away


def test_a_self_introduction_beats_a_name_said_by_others():
    units, by_id, legend = units_of(ADDRESS + [("SPEAKER_01", "My name is Bea.")])
    tag_b = next(t for t, sp in legend.items() if sp == "SPEAKER_01")
    out, _ = ground_names([{"t": tag_b, "n": "Amber", "u": "u001", "q": "Amber, are you ready", "how": "addressed"}, {"t": tag_b, "n": "Bea", "u": "u004", "q": "My name is Bea", "how": "self"}], by_id, legend)
    assert [n.name for n in out] == ["Bea"]


# ---------------------------------------------------------------------------------------------- accept / reject fixes
def prepared(wav, monkeypatch, job="d1"):
    fake = install(monkeypatch)
    run_phase1(wav, job_id=job)
    run_phase2(job)
    return fake


def test_rejecting_and_resetting_a_fix_rebuilds_the_refined_transcript(wav, monkeypatch):
    prepared(wav, monkeypatch)
    assert "PSNR metric" in load_phase2("d1").refined.segments[5].text
    p2 = corrections.decide("d1", "edt_001", "reject")
    assert "pee ess en are" in p2.refined.segments[5].text and p2.edits[0].user_decision == "reject"
    assert "PSNR" in corrections.decide("d1", "edt_001", "reset").refined.segments[5].text


def test_the_minutes_use_the_decisions_the_user_made(wav, monkeypatch):
    fake = prepared(wav, monkeypatch)
    corrections.decide("d1", "edt_001", "reject")
    r = run_phase3("d1")
    assert "pee ess en are" in [c for c in fake.calls if c[0] == "extract"][0][3] and [e.status for e in r.edits] == ["suggested"]


def test_a_suggested_fix_can_be_accepted_but_a_blocked_one_never(wav, monkeypatch):
    fake = install(monkeypatch)
    fake.r_refine = lambda user: {"edits": [{"u": "u001", "find": "25 euro", "rep": "52 euro", "k": "term"}, {"u": "u006", "find": "pee ess en are", "rep": "PSNR", "k": "acronym"}]}
    run_phase1(wav, job_id="d2")
    p2 = run_phase2("d2")
    blocked = next(e for e in p2.edits if e.status == "rejected")
    with pytest.raises(PipelineError) as e:
        corrections.decide("d2", blocked.edit_id, "accept")
    assert e.value.code == "EDIT_BLOCKED"
    ok = next(e for e in p2.edits if e.status != "rejected")
    p2 = load_phase2("d2")
    p2.edits[p2.edits.index(next(x for x in p2.edits if x.edit_id == ok.edit_id))].status = "suggested"
    (Path(settings.outputs_dir) / "d2" / "phase2.json").write_text(p2.model_dump_json())
    out = corrections.decide("d2", ok.edit_id, "accept")
    assert "PSNR" in out.refined.segments[5].text
    with pytest.raises(PipelineError):
        corrections.decide("d2", "edt_999", "accept")
    with pytest.raises(PipelineError):
        corrections.decide("d2", ok.edit_id, "maybe")


# ---------------------------------------------------------------------------------------------- typed speaker names
def test_typed_names_rebuild_the_outputs_and_survive_a_rerun(wav, monkeypatch):
    fake = install(monkeypatch)
    run_pipeline(wav, job_id="n1")
    n = len(fake.calls)
    corrections.rename_speakers("n1", {"SPEAKER_00": "Dana", "SPEAKER_01": "Bea"})
    assert len(fake.calls) == n  # no model call
    r = load_result("n1")
    assert {x.speaker: (x.name, x.basis) for x in r.speaker_names} == {"SPEAKER_00": ("Dana", "edited"), "SPEAKER_01": ("Bea", "edited")}  # Bea replaced the model's Alima
    d = Path(settings.outputs_dir) / "n1"
    assert "Dana" in (d / "report.html").read_text() and "Alima" not in (d / "minutes.md").read_text().split("## Speakers")[1].split("## Topics")[0]
    assert next(s for s in r.refined.segments if s.speaker == "SPEAKER_00").speaker_name == "Dana"
    assert {x.speaker: x.name for x in run_phase3("n1").speaker_names}["SPEAKER_00"] == "Dana"
    corrections.rename_speakers("n1", {"SPEAKER_00": ""})
    assert "SPEAKER_00" not in {x.speaker for x in load_result("n1").speaker_names}


def test_typed_names_are_validated(wav, monkeypatch):
    install(monkeypatch)
    run_phase1(wav, job_id="n2")
    for names, code in [({"SPEAKER_99": "Dana"}, "UNKNOWN_SPEAKER"), ({"SPEAKER_00": "<b>x</b>"}, "BAD_NAME"), ({"SPEAKER_00": "Dana", "SPEAKER_01": "dana"}, "DUPLICATE_NAME")]:
        with pytest.raises(PipelineError) as e:
            corrections.rename_speakers("n2", names)
        assert e.value.code == code


# ---------------------------------------------------------------------------------------------- insights
def full(wav, monkeypatch, job="i1", meeting=None):
    fake = install(monkeypatch)
    run_phase1(wav, job_id=job)
    if meeting:
        (Path(settings.outputs_dir) / job / "upload.json").write_text(json.dumps({"filename": "m.wav", "meeting_date": meeting}))
    run_phase2(job)
    return run_phase3(job)


def test_speaker_summary_tasks_by_owner_and_timeline(wav, monkeypatch):
    r = full(wav, monkeypatch)
    sp = {a["speaker"]: a for a in insights.speaker_summary(r)}
    assert len(sp["SPEAKER_02"]["commitments"]) == 1 and sum(a["share"] for a in sp.values()) in range(97, 104)
    assert any(q.kind == "question" for q in sp["SPEAKER_03"]["questions"]) or sp["SPEAKER_03"]["lines"]
    owners = dict(insights.tasks_by_owner(r))
    assert set(owners) == {"Speaker 3", "Jessie"}
    assert [x.kind for x in insights.timeline(r)] == ["proposal", "decision"] and insights.timeline(r)[0].start <= insights.timeline(r)[1].start


def test_email_and_checklist_are_plain_text_from_the_checked_records(wav, monkeypatch):
    r = full(wav, monkeypatch)
    mail = insights.email_text(r)
    assert mail.startswith("Subject: Meeting summary") and "ACTION ITEMS" in mail and "owner: Jessie" in mail and "deadline: by Friday" in mail
    steps = insights.checklist_md(r)
    assert "- [ ] Send the budget report. (deadline: by Friday)" in steps and "## Jessie" in steps
    d = Path(settings.outputs_dir) / "i1"
    assert (d / "email.txt").is_file() and (d / "next_steps.md").is_file()


def test_calendar_events_are_only_made_for_deadlines_that_can_be_dated(wav, monkeypatch):
    r = full(wav, monkeypatch, job="c1")  # no meeting date: "by Friday" cannot be dated
    assert not (Path(settings.outputs_dir) / "c1" / "action_items.ics").exists() and insights.ics(r)[1:] == (0, 1)
    r2 = full(wav, monkeypatch, job="c2", meeting="2026-02-09")  # a Monday
    text, events, skipped = insights.ics(r2)
    assert events == 1 and skipped == 0 and "DTSTART;VALUE=DATE:20260213" in text and text.startswith("BEGIN:VCALENDAR") and "\r\n" in text
    assert (Path(settings.outputs_dir) / "c2" / "action_items.ics").is_file() and r2.meeting_date == "2026-02-09"


@pytest.mark.parametrize("text,meeting,expected", [
    ("2026-05-31", None, date(2026, 5, 31)), ("March 3rd", date(2026, 2, 9), date(2026, 3, 3)), ("March 3rd", None, None), ("3rd of March 2027", None, date(2027, 3, 3)),
    ("by Friday", date(2026, 2, 9), date(2026, 2, 13)), ("by Friday", None, None), ("next Friday", date(2026, 2, 9), None), ("end of the month", date(2026, 2, 9), None),
    ("tomorrow", date(2026, 2, 9), date(2026, 2, 10)), ("market 5 units", date(2026, 2, 9), None), ("31st of February 2026", None, None), ("soon", date(2026, 2, 9), None)])
def test_deadline_resolution_never_guesses(text, meeting, expected):
    got = insights.resolve_deadline(text, meeting)
    assert (got[0] if got else None) == expected


def test_the_report_has_speaker_cards_owners_timeline_and_links_into_the_transcript(wav, monkeypatch):
    r = full(wav, monkeypatch)
    html = (Path(settings.outputs_dir) / r.job_id / "report.html").read_text()
    for part in ("Tasks by owner", "Decision timeline", "Committed to", "talk time", 'id="t-seg_0001"', 'href="#t-seg_0002"', 'class="cited"', "Transcript"):
        assert part in html, part
    assert "<script" not in html


# ---------------------------------------------------------------------------------------------- long meetings and the API
def test_a_call_that_would_be_too_big_for_the_per_minute_limit_is_refused_clearly(wav, monkeypatch):
    fake = install(monkeypatch)
    run_phase1(wav, job_id="l1")
    monkeypatch.setattr(settings, "max_call_tokens", 200)
    with pytest.raises(PipelineError) as e:
        run_phase3("l1")
    assert e.value.code == "TOO_LONG_FOR_ONE_CALL" and "PHASE3_MAX_CALLS" in e.value.message and fake.calls == []


def wait(c, job):
    for _ in range(200):
        j = c.get(f"/jobs/{job}").json()
        if j["stage"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_api_decisions_names_downloads_and_meeting_date(wav, monkeypatch):
    install(monkeypatch)
    c = TestClient(api.app)
    assert c.post("/process", files={"file": ("a.wav", b"x")}, data={"meeting_date": "next friday"}).status_code == 400
    with open(wav, "rb") as f:
        job = c.post("/process", files={"file": ("m.wav", f)}, data={"through": "3", "meeting_date": "2026-02-09"}).json()["job_id"]
    j = wait(c, job)
    assert j["result"]["meeting_date"] == "2026-02-09" and not j.get("stale_result")
    assert "Subject:" in c.get(f"/jobs/{job}/download?format=email").text and "Next steps" in c.get(f"/jobs/{job}/download?format=steps").text
    assert "BEGIN:VCALENDAR" in c.get(f"/jobs/{job}/download?format=ics").text
    d = Path(settings.outputs_dir) / job
    old = time.time() - 30
    os.utime(d / "result.json", (old, old))
    j = c.post(f"/jobs/{job}/edits/edt_001", json={"decision": "reject"}).json()
    assert j["phase2"]["edits"][0]["user_decision"] == "reject" and j["stale_result"] is True
    assert c.post(f"/jobs/{job}/edits/edt_999", json={"decision": "reject"}).status_code == 404
    assert c.post(f"/jobs/{job}/edits/edt_001", json={"decision": "maybe"}).status_code == 400
    j = c.post(f"/jobs/{job}/speakers", json={"names": {"SPEAKER_00": "Dana"}}).json()
    assert any(n["name"] == "Dana" for n in j["result"]["speaker_names"])
    assert c.post(f"/jobs/{job}/speakers", json={"names": {"SPEAKER_77": "Dana"}}).status_code == 400
    assert c.post(f"/jobs/{job}/speakers", json={"nope": 1}).status_code == 400
    assert "Dana" in c.get(f"/jobs/{job}/report").text


# ---------------------------------------------------------------------------------------------- phase 2: who is who
INTRO = [("SPEAKER_00", "Hello everyone."), ("SPEAKER_01", "Hi, I'm Bea, the project manager."), ("SPEAKER_00", "Great, let's start.")]


def who_job(monkeypatch, rows, people, job):
    fake = install(monkeypatch, transcript=make_transcript(rows))
    monkeypatch.setattr(settings, "names_enabled", True)
    fake.r_refine = lambda user: {"edits": []}
    fake.r_extract = lambda user: {"items": [], "topics": [], "names": []}
    fake.r_names = lambda user: {"people": people}
    return fake


def test_phase_2_reads_names_and_roles_from_the_conversation(wav, monkeypatch):
    people = [{"t": "B", "n": "Bea", "r": "project manager", "u": "u002", "q": "I'm Bea, the project manager", "how": "self"},
              {"t": "A", "n": "Zed", "u": "u003", "q": "Great, let's start", "how": "self"},  # name not in the quote
              {"t": "A", "r": "boss", "u": "u003", "q": "Great, let's start", "how": "self"},  # role not in the quote
              {"t": "A", "n": "Bea", "u": "u001", "q": "Hello everyone", "how": "self"}]  # not in the quote either
    fake = who_job(monkeypatch, INTRO, people, "w1")
    run_phase1(wav, job_id="w1")
    p2 = run_phase2("w1")
    assert [c[0] for c in fake.calls] == ["names", "refine"]
    assert [(x.speaker, x.name, x.role, x.basis, x.status) for x in p2.name_suggestions] == [("SPEAKER_01", "Bea", "project manager", "self", "applied")]
    r = run_phase3("w1")
    assert [(n.speaker, n.name) for n in r.speaker_names] == [("SPEAKER_01", "Bea")] and r.speaker_roles == {"SPEAKER_01": "project manager"}
    assert next(s for s in r.refined.segments if s.speaker == "SPEAKER_01").speaker_name == "Bea"
    html = (Path(settings.outputs_dir) / "w1" / "report.html").read_text()
    assert "Bea" in html and "project manager" in html


def test_a_name_said_to_someone_is_only_suggested_until_the_user_accepts_it(wav, monkeypatch):
    rows = [("SPEAKER_00", "Amber, are you ready for the presentation?"), ("SPEAKER_01", "Yes I am, thanks."), ("SPEAKER_00", "Great, go ahead.")]
    who_job(monkeypatch, rows, [{"t": "B", "n": "Amber", "u": "u001", "q": "Amber, are you ready", "how": "addressed"}], "w2")
    run_phase1(wav, job_id="w2")
    p2 = run_phase2("w2")
    assert [(x.name, x.status) for x in p2.name_suggestions] == [("Amber", "suggested")]
    assert run_phase3("w2").speaker_names == []  # suggested, not used
    corrections.decide_name("w2", "who_001", "accept")
    assert [(n.name, n.basis) for n in load_result("w2").speaker_names] == [("Amber", "addressed")]  # report rebuilt, no model call
    assert "Amber" in (Path(settings.outputs_dir) / "w2" / "report.html").read_text()
    corrections.decide_name("w2", "who_001", "reject")
    assert load_result("w2").speaker_names == []
    with pytest.raises(PipelineError):
        corrections.decide_name("w2", "who_999", "accept")
    with pytest.raises(PipelineError):
        corrections.decide_name("w2", "who_001", "maybe")


def test_rejecting_a_self_introduction_removes_it_and_a_typed_name_wins(wav, monkeypatch):
    people = [{"t": "B", "n": "Bea", "u": "u002", "q": "I'm Bea", "how": "self"}]
    who_job(monkeypatch, INTRO, people, "w3")
    run_phase1(wav, job_id="w3")
    run_phase2("w3")
    run_phase3("w3")
    corrections.decide_name("w3", "who_001", "reject")
    assert load_result("w3").speaker_names == []
    corrections.decide_name("w3", "who_001", "reset")
    corrections.rename_speakers("w3", {"SPEAKER_01": "Cleo"})
    assert [(n.name, n.basis) for n in load_result("w3").speaker_names] == [("Cleo", "edited")]
    assert [(n.name, n.basis) for n in run_phase3("w3").speaker_names] == [("Cleo", "edited")]  # survives a rerun


def test_phase_2_skips_the_names_call_when_nobody_can_be_told_apart(wav, monkeypatch):
    fake = install(monkeypatch, known=False)
    monkeypatch.setattr(settings, "names_enabled", True)
    monkeypatch.setattr(settings, "speakers_enabled", False)
    run_phase1(wav, job_id="w4")
    p2 = run_phase2("w4")
    assert "names" not in [c[0] for c in fake.calls] and p2.name_suggestions == []


def test_names_found_by_the_extraction_still_count_and_phase_2_names_come_first(wav, monkeypatch):
    fake = install(monkeypatch)  # the fake extraction names SPEAKER_01 "Alima"
    monkeypatch.setattr(settings, "names_enabled", True)
    t = make_transcript()
    tag = next(k for k, sp in build_units(t)[1].items() if sp == "SPEAKER_03")
    from tests.support import uid as unit_id

    fake.r_names = lambda user: {"people": [{"t": tag, "n": "Jessie", "u": unit_id(t, 3), "q": "Jessie, can you check the supplier list", "how": "addressed"}]}
    run_phase1(wav, job_id="w5")
    p2 = run_phase2("w5")
    assert [(x.name, x.status) for x in p2.name_suggestions] == [("Jessie", "suggested")]
    corrections.decide_name("w5", "who_001", "accept")
    r = run_phase3("w5")
    assert {n.speaker: n.name for n in r.speaker_names} == {"SPEAKER_03": "Jessie", "SPEAKER_01": "Alima"}
    assert [n.name for n in r.extracted_names] == ["Alima"]


def test_api_name_decisions(wav, monkeypatch):
    people = [{"t": "B", "n": "Bea", "u": "u002", "q": "I'm Bea", "how": "self"}]
    who_job(monkeypatch, INTRO, people, "w6")
    c = TestClient(api.app)
    with open(wav, "rb") as f:
        job = c.post("/process", files={"file": ("m.wav", f)}, data={"through": "3"}).json()["job_id"]
    j = wait(c, job)
    assert j["phase2"]["name_suggestions"][0]["name"] == "Bea" and j["result"]["speaker_names"][0]["name"] == "Bea"
    j = c.post(f"/jobs/{job}/names/who_001", json={"decision": "reject"}).json()
    assert j["phase2"]["name_suggestions"][0]["user_decision"] == "reject" and j["result"]["speaker_names"] == []
    assert c.post(f"/jobs/{job}/names/who_404", json={"decision": "accept"}).status_code == 404
    assert c.post(f"/jobs/{job}/names/who_001", json={"decision": "maybe"}).status_code == 400


# ---------------------------------------------------------------------------------------------- the required outputs
def test_raw_and_refined_transcripts_are_separate_files_and_the_raw_one_is_never_overwritten(wav, monkeypatch):
    full(wav, monkeypatch, job="o1")
    d = Path(settings.outputs_dir) / "o1"
    raw, refined = (d / "raw_transcript.txt").read_text(), (d / "refined_transcript.txt").read_text()
    assert "pee ess en are" in raw and "PSNR metric" not in raw  # speech-to-text before refinement
    assert "PSNR metric" in refined and "pee ess en are" not in refined  # after refinement
    assert not (d / "transcript.txt").exists()
    c = TestClient(api.app)
    assert "pee ess en are" in c.get("/jobs/o1/download?format=raw").text and "PSNR metric" in c.get("/jobs/o1/download?format=refined").text


def test_the_machine_readable_record_has_the_five_outputs_and_says_unspecified(wav, monkeypatch):
    full(wav, monkeypatch, job="o2")
    j = json.loads((Path(settings.outputs_dir) / "o2" / "minutes.json").read_text())
    assert {"transcript", "refined", "minutes", "decisions", "action_items"} <= set(j)
    acts = {a["task"]: a for a in j["action_items"]}
    assert acts["Send the budget report."]["owner"] == "Speaker 3" and acts["Send the budget report."]["deadline"] == "by Friday"
    assert acts["Check the supplier list."]["owner"] == "Jessie" and acts["Check the supplier list."]["deadline"] == "unspecified"
    assert j["decisions"][0]["decision"] and j["decisions"][0]["evidence"]["quote"] and j["minutes"]["headline"] and j["minutes"]["topics"]
    md = (Path(settings.outputs_dir) / "o2" / "minutes.md").read_text()
    assert "Check the supplier list." in md and "Owner: Jessie | Deadline: unspecified" in md  # the human-readable record says the same


def test_empty_decisions_and_action_items_are_empty_lists_not_missing(wav, monkeypatch):
    fake = install(monkeypatch)
    fake.r_extract = lambda user: {"items": [{"t": "question", "s": "open", "x": "Who owns it?", "q": [["u001", "decide the selling price"]]}], "topics": [], "names": []}
    run_phase1(wav, job_id="o3")
    run_phase2("o3")
    r = run_phase3("o3")
    j = json.loads((Path(settings.outputs_dir) / "o3" / "minutes.json").read_text())
    assert j["decisions"] == [] and j["action_items"] == [] and "None recorded in this meeting." in (Path(settings.outputs_dir) / "o3" / "minutes.md").read_text()
    assert any("No decision was recorded" in x for x in insights.attention(r))


def test_needs_attention_counts_what_a_reader_should_check(wav, monkeypatch):
    r = full(wav, monkeypatch, job="o4")
    att = insights.attention(r)
    assert any("no stated deadline" in x for x in att) and any("question(s) were left open" in x for x in att)
    html = (Path(settings.outputs_dir) / "o4" / "report.html").read_text()
    assert "Needs attention" in html and "## Needs attention" in (Path(settings.outputs_dir) / "o4" / "minutes.md").read_text()
