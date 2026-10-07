"""The meeting workspace API end to end, with a fake recogniser and fake models: upload, checkpoints, decisions, report, exports, seeking, failure and recovery."""
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import app.api as api
import app.meetings as meetings
from app.config.settings import settings
from app.core import session
from tests.support import install, make_transcript

INTRO = [("SPEAKER_00", "Hello everyone."), ("SPEAKER_01", "Hi, I'm Bea, the project manager."), ("SPEAKER_00", "Great, the pee ess en are is up."),
         ("SPEAKER_01", "I'll send the budget report by Friday."), ("SPEAKER_00", "Agreed, let's go with 25 euro.")]


@pytest.fixture
def client(monkeypatch):
    fake = install(monkeypatch, transcript=make_transcript(INTRO))
    monkeypatch.setattr(settings, "names_enabled", True)
    fake.r_names = lambda user: {"people": [{"t": "B", "n": "Bea", "r": "project manager", "u": "u002", "q": "I'm Bea, the project manager", "how": "self"}]}
    fake.r_refine = lambda user: {"edits": [{"u": "u003", "find": "pee ess en are", "rep": "PSNR", "k": "acronym"}]}
    fake.r_extract = lambda user: {"items": [
        {"t": "action", "s": "committed", "x": "Send the budget report.", "o": "first", "ou": "u004", "d": "by Friday", "q": [["u004", "I'll send the budget report by Friday"]]},
        {"t": "decision", "s": "confirmed", "x": "The group agreed to a price of 25 euro.", "q": [["u005", "Agreed, let's go with 25 euro"]]}], "topics": [{"n": "Price", "a": "u001", "b": "u005"}], "names": []}
    for _ in range(200):  # a previous test's background run must have finished before this one starts
        if not meetings.RUNNING:
            break
        time.sleep(0.02)
    meetings.RUNNING.clear()
    yield TestClient(api.app), fake
    for _ in range(200):
        if not meetings.RUNNING:
            break
        time.sleep(0.02)


def upload(c, wav, mode="review", date=""):
    with open(wav, "rb") as f:
        r = c.post("/meetings", files={"file": ("standup.wav", f)}, data={"mode": mode, "meeting_date": date})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def wait(c, job, until=lambda s: s["status"] in ("speakers_ready", "refinement_ready", "report_ready", "failed") and not meetings.RUNNING):
    for _ in range(300):
        s = c.get(f"/meetings/{job}").json()
        if until(s["session"]):
            return s
        time.sleep(0.03)
    raise AssertionError("timeout: " + json.dumps(c.get(f"/meetings/{job}").json()["session"]["stages"]))


def test_the_whole_flow_with_human_checkpoints(client, wav):
    c, fake = client
    job = upload(c, wav, date="2026-02-09")
    s = wait(c, job)["session"]
    assert s["status"] == "speakers_ready" and s["awaiting"] == "speakers" and s["filename"] == "standup.wav" and s["meeting_date"] == "2026-02-09"
    assert {k: v["state"] for k, v in s["stages"].items()} == {"audio": "completed", "transcription": "completed", "speakers": "needs_review", "refinement": "waiting", "report": "waiting"}
    # raw transcript is available at once and never changes
    raw = c.get(f"/meetings/{job}/transcript").json()
    assert raw["refined"] is None and len(raw["raw"]) == 5 and raw["raw"][0]["speaker"] == "Speaker 1" and "pee ess en are" in raw["raw"][2]["text"]
    # speakers: a name was proposed with its evidence, and nothing is asserted until the user decides
    sp = c.get(f"/meetings/{job}/speakers").json()
    two = next(x for x in sp["speakers"] if x["label"] == "SPEAKER_01")
    assert sp["diarized"] and two["display"] == "Speaker 2" and two["suggestions"][0]["name"] == "Bea" and two["suggestions"][0]["evidence_text"] == "Said by the speaker themselves" and two["suggestions"][0]["quote"]
    assert [o["segment_id"] for o in two["occurrences"]][:2] == ["seg_0002", "seg_0004"] and two["lines"] == 2
    # continue past the speaker checkpoint: refinement runs and stops for review
    assert c.post(f"/meetings/{job}/continue", json={"stage": "speakers"}).status_code == 200
    s = wait(c, job, lambda s: s["status"] == "refinement_ready" and not meetings.RUNNING)["session"]
    assert s["awaiting"] == "refinement"
    rf = c.get(f"/meetings/{job}/refinement").json()
    ch = rf["changes"][0]
    assert rf["counts"]["proposed"] == 1 and ch["original"] == "pee ess en are" and ch["proposed"] == "PSNR" and ch["reason"] == "Acronym normalised" and "pee ess en are" not in ch["before"] + ch["after"]
    assert ch["state"] in ("needs_review", "accepted")
    # the user accepts everything waiting, then continues: the report is generated
    after = c.post(f"/meetings/{job}/edits", json={"action": "accept_suggested"}).json()
    assert after["counts"]["accepted"] == 1 and after["counts"]["needs_review"] == 0
    c.post(f"/meetings/{job}/continue", json={"stage": "refinement"})
    s = wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)["session"]
    assert s["awaiting"] is None and all(v["state"] == "completed" for v in s["stages"].values())
    rep = c.get(f"/meetings/{job}/report").json()
    assert rep["ready"] and rep["decisions"][0]["text"] == "The group agreed to a price of 25 euro." and rep["decisions"][0]["evidence"]["segment_id"]
    act = rep["action_items"][0]
    assert act["owner"] == "Bea" and act["deadline"] == "by Friday" and act["evidence"]["quote"]  # her self-introduction was applied
    # the refined transcript now exists beside the untouched raw one
    both = c.get(f"/meetings/{job}/transcript").json()
    assert "PSNR" in both["refined"][2]["text"] and "pee ess en are" in both["raw"][2]["text"] and both["refined"][2]["changes"][0]["corrected"] == "PSNR"


def test_auto_mode_goes_straight_to_the_report(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto")
    s = wait(c, job, lambda s: s["status"] in ("report_ready", "failed") and not meetings.RUNNING)["session"]
    assert s["status"] == "report_ready" and s["mode"] == "auto"


def test_exports_exist_only_when_made_and_have_professional_names(client, wav):
    c, _ = client
    job = upload(c, wav, date="2026-02-09")
    wait(c, job)
    ex = {e["key"]: e for e in c.get(f"/meetings/{job}/exports").json()}
    assert ex["raw_transcript_txt"]["ready"] and ex["raw_transcript_txt"]["filename"] == "meeting_2026-02-09_raw_transcript.txt"
    assert not ex["report_pdf"]["ready"] and "report has been generated" in ex["report_pdf"]["reason"] and not ex["refined_transcript_txt"]["ready"]
    assert c.get(f"/meetings/{job}/export/report_pdf").status_code == 404  # never a half-made file
    r = c.get(f"/meetings/{job}/export/raw_transcript_txt")
    assert r.status_code == 200 and 'filename="meeting_2026-02-09_raw_transcript.txt"' in r.headers["content-disposition"] and "pee ess en are" in r.text
    c.post(f"/meetings/{job}/continue", json={"stage": "speakers"})
    wait(c, job, lambda s: s["status"] == "refinement_ready" and not meetings.RUNNING)
    c.post(f"/meetings/{job}/edits", json={"action": "accept_suggested"})
    c.post(f"/meetings/{job}/continue", json={"stage": "refinement"})
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    ex = {e["key"]: e for e in c.get(f"/meetings/{job}/exports").json()}
    assert all(ex[k]["ready"] for k in ("refined_transcript_txt", "report_pdf", "report_md", "record_json", "package_zip", "email_txt", "actions_csv"))
    assert ex["actions_ics"]["ready"] and ex["actions_ics"]["filename"] == "meeting_2026-02-09_action_items.ics"  # a meeting date was given, so "by Friday" can be dated
    assert "DTSTART;VALUE=DATE:20260213" in c.get(f"/meetings/{job}/export/actions_ics").text
    pdf = c.get(f"/meetings/{job}/export/report_pdf")
    assert pdf.content.startswith(b"%PDF") and 'filename="meeting_2026-02-09_report.pdf"' in pdf.headers["content-disposition"]
    rec = c.get(f"/meetings/{job}/export/record_json").json()
    assert rec["decisions"][0]["decision"] == "The group agreed to a price of 25 euro." and rec["action_items"][0]["owner"] == "Bea" and rec["action_items"][0]["deadline"] == "by Friday" and rec["meeting"]["date"] == "2026-02-09"
    ref = c.get(f"/meetings/{job}/export/refined_transcript_json").json()
    assert ref["proposed_corrections"][0]["in_force"] is True and ref["segments"][2]["changes"]
    raw = c.get(f"/meetings/{job}/export/raw_transcript_json").json()
    assert raw["kind"] == "raw_transcript" and "pee ess en are" in raw["segments"][2]["text"]
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/meetings/{job}/export/package_zip").content))
    names = sorted(z.namelist())
    assert "meeting_2026-02-09/meeting_2026-02-09_report.pdf" in names and "meeting_2026-02-09/meeting_2026-02-09_raw_transcript.txt" in names and len(names) >= 9


def test_the_human_readable_and_machine_readable_records_say_the_same_things(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    md = c.get(f"/meetings/{job}/export/report_md").text
    rec = c.get(f"/meetings/{job}/export/record_json").json()
    for d in rec["decisions"]:
        assert d["decision"] in md
    for a in rec["action_items"]:
        assert a["task"] in md


def test_the_recording_can_be_played_and_seeked(client, wav):
    c, _ = client
    job = upload(c, wav)
    wait(c, job)
    full = c.get(f"/meetings/{job}/audio")
    part = c.get(f"/meetings/{job}/audio", headers={"Range": "bytes=0-99"})
    assert full.status_code == 200 and part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")


def test_an_unusable_file_is_explained_not_dumped(client, tmp_path):
    c, _ = client
    r = c.post("/meetings", files={"file": ("empty.wav", b"")})
    job = r.json()["id"]
    s = wait(c, job, lambda s: s["status"] == "failed")["session"]
    err = s["stages"]["audio"]["error"]
    assert err["code"] == "EMPTY_FILE" and err["title"] == "The file is empty." and "Choose a recording" in err["action"] and "Traceback" not in json.dumps(s)
    assert s["stages"]["transcription"]["state"] == "waiting"
    assert c.get(f"/meetings/{job}/transcript").status_code == 409
    t = c.post("/meetings", files={"file": ("notes.txt", b"hello")}).json()["id"]
    e = wait(c, t, lambda s: s["status"] == "failed")["session"]["stages"]["audio"]["error"]
    assert e["code"] == "UNSUPPORTED_FORMAT" and "WAV" in e["action"]


def test_a_failed_report_can_be_retried_and_the_transcript_is_still_there(client, wav):
    c, fake = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    fake.fail = {"extract"}
    c.post(f"/meetings/{job}/retry", json={"stage": "report"})
    s = wait(c, job, lambda s: s["status"] == "failed" and not meetings.RUNNING)["session"]
    assert s["stages"]["report"]["error"]["code"] == "ANALYSIS_FAILED" and s["stages"]["refinement"]["state"] == "completed"
    assert c.get(f"/meetings/{job}/transcript").status_code == 200 and any(i["severity"] == "error" for i in c.get(f"/meetings/{job}").json()["issues"])
    fake.fail = set()
    c.post(f"/meetings/{job}/retry", json={"stage": "report"})
    s = wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)["session"]
    assert s["stages"]["report"]["attempts"] == 3


def test_a_step_interrupted_by_a_restart_is_shown_as_interrupted_with_a_way_forward(client, wav):
    c, _ = client
    job = upload(c, wav)
    wait(c, job)
    session.update(job, lambda s: s["stages"]["refinement"].update(state="processing", message="Checking"))
    meetings.RUNNING.clear()
    s = c.get(f"/meetings/{job}").json()["session"]
    assert s["stages"]["refinement"]["state"] == "failed" and s["stages"]["refinement"]["error"]["code"] == "INTERRUPTED" and "Retry this step" in s["stages"]["refinement"]["error"]["action"]


def test_a_correction_changed_after_the_report_marks_it_out_of_date_but_a_rename_just_rebuilds_it(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    assert c.get(f"/meetings/{job}").json()["report_stale"] is False
    time.sleep(1.1)
    c.post(f"/meetings/{job}/speaker-names", json={"names": {"SPEAKER_00": "Dana"}})
    assert c.get(f"/meetings/{job}").json()["report_stale"] is False and "Dana" in c.get(f"/meetings/{job}/export/report_md").text  # names reach the report at once, no model call
    time.sleep(1.1)
    c.post(f"/meetings/{job}/edits/edt_001", json={"decision": "reject"})  # the report was written from the corrected transcript
    v = c.get(f"/meetings/{job}").json()
    assert v["report_stale"] is True and "Regenerate" in v["reason"]


def test_without_a_meeting_date_a_relative_deadline_is_not_turned_into_a_calendar_event(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    ics = next(e for e in c.get(f"/meetings/{job}/exports").json() if e["key"] == "actions_ics")
    assert not ics["ready"] and "calendar date" in ics["reason"] and c.get(f"/meetings/{job}/export/actions_ics").status_code == 404


def test_speaker_names_the_user_types_reach_the_transcript_views(client, wav):
    c, _ = client
    job = upload(c, wav)
    wait(c, job)
    sp = c.post(f"/meetings/{job}/speaker-names", json={"names": {"SPEAKER_00": "Dana"}}).json()
    assert next(x for x in sp["speakers"] if x["label"] == "SPEAKER_00")["name"] == "Dana" and sp["typed_names"] == {"SPEAKER_00": "Dana"}
    assert c.get(f"/meetings/{job}/transcript").json()["raw"][0]["speaker_name"] == "Dana"
    assert c.post(f"/meetings/{job}/speaker-names", json={"names": {"SPEAKER_77": "X"}}).status_code == 400
    assert c.post(f"/meetings/{job}/names/who_001", json={"decision": "reject"}).json()["speakers"]
    assert c.post(f"/meetings/{job}/continue", json={"stage": "report"}).status_code == 400


def test_pipeline_details_show_stage_outputs_and_keep_implementation_details_behind_an_explicit_switch(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    p = c.get(f"/meetings/{job}/pipeline").json()
    assert [s["key"] for s in p["stages"]] == ["audio", "transcription", "speakers", "refinement", "report"] and "developer" not in p
    text = json.dumps(p)
    assert "gpt-oss" not in text and "qwen" not in text and "token" not in text.lower()
    assert "pee ess en are" in next(s for s in p["stages"] if s["key"] == "transcription")["output"]["content"]
    dev = c.get(f"/meetings/{job}/pipeline?developer=true").json()["developer"]
    assert dev and {"model", "stage", "prompt_tokens"} <= set(dev[0])


def test_the_list_unknown_ids_and_bad_input_are_handled(client, wav):
    c, _ = client
    job = upload(c, wav)
    wait(c, job)
    assert any(m["id"] == job and m["filename"] == "standup.wav" for m in c.get("/meetings").json())
    assert c.get("/meetings/nope").status_code == 404 and c.get("/meetings/../x").status_code in (404, 422)
    assert c.post("/meetings", files={"file": ("a.wav", b"x")}, data={"meeting_date": "next friday"}).status_code == 400
    assert c.post("/meetings", files={"file": ("a.wav", b"x")}, data={"mode": "sometimes"}).status_code == 400
    assert c.get(f"/meetings/{job}/export/exe").status_code == 400


def test_nothing_a_user_can_read_names_a_model_a_service_or_a_token_count(client, wav):
    c, _ = client
    job = upload(c, wav, mode="auto", date="2026-02-09")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    import re

    bad = re.compile(r"gpt|qwen|llama|nvidia|groq|deepgram|whisper|openai|\btokens?\b|\bmodel\b|prompt", re.I)
    for key in ("report_md", "report_html", "email_txt", "record_json", "refined_transcript_json", "raw_transcript_txt"):
        text = c.get(f"/meetings/{job}/export/{key}").text
        m = bad.search(text)
        assert not m, (key, text[max(0, m.start() - 90): m.end() + 60])
    view = json.dumps({"session": c.get(f"/meetings/{job}").json(), "report": c.get(f"/meetings/{job}/report").json(), "pipeline": c.get(f"/meetings/{job}/pipeline").json()})
    m = bad.search(view)
    assert not m, view[max(0, m.start() - 120): m.end() + 60]
    pdf_text = c.get(f"/meetings/{job}/export/report_pdf").content
    assert b"gpt" not in pdf_text.lower() and b"qwen" not in pdf_text.lower()


# ---------------------------------------------------------------------------------------------- the saved example meeting
def test_the_saved_example_is_seeded_listed_readable_exportable_and_never_reprocessed(client, monkeypatch):
    import app.core.llm as llm
    from app.core import examples
    from app.utils.errors import PipelineError

    def no_calls(*a, **k):
        raise PipelineError("API_ERROR", "the example must never spend a model call")

    monkeypatch.setattr(llm, "_call_model", no_calls)
    monkeypatch.setattr("app.core.pipeline.get_provider", lambda name=None: (_ for _ in ()).throw(AssertionError("the example must never call a recogniser")))
    c, _ = client
    assert examples.seed() == ["example-es2010b"] and examples.seed() == []  # copied once
    ex = next(m for m in c.get("/meetings").json() if m["id"] == "example-es2010b")
    assert ex["example"] is True and ex["status"] == "report_ready" and "example" in ex["filename"]
    v = c.get("/meetings/example-es2010b").json()
    assert v["session"]["example"] is True and "produced earlier" in v["session"]["example_note"] and all(x["state"] == "completed" for x in v["session"]["stages"].values())
    rep = c.get("/meetings/example-es2010b/report").json()
    assert rep["ready"] and rep["headline"] and rep["summary"] and rep["topics"] and rep["action_items"] and all(a["evidence"]["quote"] for a in rep["action_items"])
    assert len(c.get("/meetings/example-es2010b/transcript").json()["raw"]) > 20 and c.get("/meetings/example-es2010b/speakers").json()["diarized"]
    assert c.get("/meetings/example-es2010b/audio", headers={"Range": "bytes=0-9"}).status_code == 206
    assert c.get("/meetings/example-es2010b/export/report_pdf").content.startswith(b"%PDF")
    assert c.get("/meetings/example-es2010b/export/package_zip").status_code == 200 and c.get("/meetings/example-es2010b/pipeline").status_code == 200
    for path, body in (("continue", {"stage": "speakers"}), ("retry", {"stage": "report"})):
        r = c.post(f"/meetings/example-es2010b/{path}", json=body)
        assert r.status_code == 409 and "saved example" in r.json()["detail"]
    # typed names rebuild the report offline, which is allowed
    assert c.post("/meetings/example-es2010b/speaker-names", json={"names": {"SPEAKER_00": "Test"}}).status_code == 200
    assert c.post("/meetings/example-es2010b/speaker-names", json={"names": {"SPEAKER_00": ""}}).status_code == 200


def test_a_new_upload_is_never_served_from_the_example(client, wav):
    from app.core import examples

    examples.seed()
    c, _ = client
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    new = c.get(f"/meetings/{job}/report").json()
    assert new["decisions"][0]["text"] == "The group agreed to a price of 25 euro." and new["headline"] != c.get("/meetings/example-es2010b/report").json()["headline"]
    s = c.get(f"/meetings/{job}").json()["session"]
    assert not s.get("example") and s["filename"] == "standup.wav"
    assert all(m["id"] != job or not m["example"] for m in c.get("/meetings").json())


def test_a_meeting_made_before_sessions_existed_opens_in_every_view(client, wav):
    from app.core.pipeline import run_phase1, run_phase2, run_phase3

    c, _ = client
    run_phase1(wav, job_id="legacy1")
    run_phase2("legacy1")
    run_phase3("legacy1")  # the old three-phase functions write no session file
    assert not session.path("legacy1").exists()
    v = c.get("/meetings/legacy1").json()
    assert v["session"]["status"] == "report_ready" and v["session"]["stages"]["report"]["state"] == "completed"
    for path in ("transcript", "speakers", "refinement", "report", "pipeline", "exports"):
        r = c.get(f"/meetings/legacy1/{path}")
        assert r.status_code == 200, (path, r.status_code)
    assert c.get("/meetings/legacy1/export/report_pdf").content.startswith(b"%PDF")
    assert any(m["id"] == "legacy1" and m["status"] == "report_ready" for m in c.get("/meetings").json())


def test_when_the_final_check_cannot_run_the_report_says_so_and_never_prints_raw_labels_or_model_wording(client, wav, monkeypatch):
    c, fake = client
    monkeypatch.setattr("app.core.pipeline.RETRY_WAIT", 0)
    fake.fail = {"review"}
    job = upload(c, wav, mode="auto")
    s = wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    assert s["final_check"] is False and "final check did not run" in s["session"]["stages"]["report"]["message"]
    rep = c.get(f"/meetings/{job}/report").json()
    assert rep["final_check"] is False and rep["decisions"] and all(d["unconfirmed"] for d in rep["decisions"])
    text = " ".join(x["text"] for x in rep["summary"])
    assert "SPEAKER_" not in text and "owner:" not in text and "Send the budget report" not in text  # tasks keep their own section; the summary quotes, it does not rephrase
    assert any(x["text"].startswith("Possible decision (not confirmed") and "Agreed, let's go with 25 euro" in x["text"] for x in rep["summary"])
    assert "final check did not run" in c.get(f"/meetings/{job}/export/report_md").text
    fake.fail = set()
    c.post(f"/meetings/{job}/retry", json={"stage": "report"})
    s = wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    assert s["final_check"] is True and not any(d["unconfirmed"] for d in c.get(f"/meetings/{job}/report").json()["decisions"])


def test_a_name_that_was_only_said_to_someone_is_not_printed_as_a_participant(client, wav):
    from app.core.corrections import merge_names
    from app.core.schemas import SpeakerName

    said_to = SpeakerName(speaker="SPEAKER_02", name="Ed", uid="u009", quote="Ed, so what do you think", basis="addressed")
    self_intro = SpeakerName(speaker="SPEAKER_03", name="Bea", uid="u002", quote="I'm Bea", basis="self")
    names, _ = merge_names(None, [said_to, self_intro])
    assert [n.name for n in names] == ["Bea"]


def test_every_export_follows_the_brief_five_named_outputs_unspecified_never_guessed_and_the_same_tasks_in_every_format(client, wav):
    c, fake = client
    fake.r_extract = lambda user: {"items": [
        {"t": "action", "s": "requested", "x": "Prepare the slides.", "o": "none", "q": [["u004", "I'll send the budget report by Friday"]]},
        {"t": "decision", "s": "confirmed", "x": "The group agreed to a price of 25 euro.", "q": [["u005", "Agreed, let's go with 25 euro"]]}], "topics": [{"n": "Price", "a": "u001", "b": "u005"}], "names": []}
    job = upload(c, wav, mode="auto", date="2026-02-09")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    get = lambda k: c.get(f"/meetings/{job}/export/{k}")
    rec = get("record_json").json()
    assert {"meeting", "status", "minutes", "decisions", "action_items", "raw_transcript", "refined_transcript"} <= set(rec)
    assert not {"records", "registers", "transcript", "refined", "edits", "calls", "usage", "extracted_names"} & set(rec)  # no internal dump
    assert rec["status"]["final_check_ran"] is True and rec["minutes"]["summary"] is not None
    a = rec["action_items"][0]
    assert a["owner"] == "unspecified" and a["owner_stated"] is False and a["source"]["quote"]
    raw, refined = get("raw_transcript_txt").text, get("refined_transcript_txt").text
    assert raw.startswith("RAW TRANSCRIPT\nFile: standup.wav\nDate: 2026-02-09\nDuration:") and "has not been edited" in raw and "CORRECTIONS APPLIED" not in raw
    assert refined.startswith("REFINED TRANSCRIPT") and "CORRECTIONS APPLIED" in refined
    md, html_, csv_ = get("report_md").text, get("report_html").text, get("actions_csv").text
    for text in (md, html_, csv_):
        assert "Prepare the slides." in text
    assert "Owner: unspecified" in md and "Deadline: unspecified" in md and "unspecified" in csv_.splitlines()[1]
    assert html_.startswith("<!doctype html>") and "<script" not in html_
    names = zipfile.ZipFile(io.BytesIO(get("package_zip").content)).namelist()
    manifest = zipfile.ZipFile(io.BytesIO(get("package_zip").content)).read("meeting_2026-02-09/README.txt").decode()
    assert any(n.endswith("README.txt") for n in names) and "unspecified" in manifest and "meeting_2026-02-09_record.json" in manifest


def test_a_meeting_with_no_decisions_or_tasks_exports_empty_lists_and_says_so(client, wav):
    c, fake = client
    fake.r_extract = lambda user: {"items": [], "topics": [{"n": "Chat", "a": "u001", "b": "u005"}], "names": []}
    job = upload(c, wav, mode="auto")
    wait(c, job, lambda s: s["status"] == "report_ready" and not meetings.RUNNING)
    rec = c.get(f"/meetings/{job}/export/record_json").json()
    assert rec["decisions"] == [] and rec["action_items"] == []
    md = c.get(f"/meetings/{job}/export/report_md").text
    assert "No explicit decisions were identified" in md and "No actionable tasks were identified" in md
    assert c.get(f"/meetings/{job}/export/actions_csv").text.strip().count("\n") == 0  # header only
