"""The pipeline as a graph: stages, human checkpoints, resuming, retrying, failures, and what the persisted session says along the way."""
import pytest

import app.core.graph as g
from app.config.settings import settings
from app.core import session
from app.core.errors import describe
from app.core.pipeline import load_phase2
from app.utils.errors import PipelineError
from tests.support import install, make_transcript

INTRO = [("SPEAKER_00", "Hello everyone."), ("SPEAKER_01", "Hi, I'm Bea, the project manager."), ("SPEAKER_00", "Great, the pee ess en are is up.")]


def states(job):
    return {k: v["state"] for k, v in session.load(job)["stages"].items()}


def start(wav, monkeypatch, job="g1", **kw):
    fake = install(monkeypatch, transcript=make_transcript(INTRO))
    monkeypatch.setattr(settings, "names_enabled", True)
    fake.r_names = lambda user: {"people": [{"t": "B", "n": "Bea", "u": "u002", "q": "I'm Bea", "how": "self"}]}
    fake.r_refine = lambda user: {"edits": [{"u": "u003", "find": "pee ess en are", "rep": "PSNR", "k": "acronym"}]}
    fake.r_extract = lambda user: {"items": [{"t": "action", "s": "committed", "x": "Introduce the project.", "o": "first", "ou": "u002", "d": None, "q": [["u002", "I'm Bea, the project manager"]]}], "topics": [], "names": []}
    session.create(job, "meeting.wav", None, kw.pop("mode", "review"))
    return fake


def test_review_mode_stops_at_the_speaker_checkpoint_and_each_approval_moves_one_step_on(wav, monkeypatch):
    fake = start(wav, monkeypatch)
    out = g.run("g1", str(wav), mode="review")
    assert out["outcome"] == "review" and states("g1") == {"audio": "completed", "transcription": "completed", "speakers": "needs_review", "refinement": "waiting", "report": "waiting"}
    s = session.load("g1")
    assert s["status"] == "speakers_ready" and s["awaiting"] == "speakers" and s["audio"]["duration_seconds"] > 0
    assert [c[0] for c in fake.calls] == ["names"]  # nothing past the checkpoint has run
    g.approve("g1", "speakers")
    g.run("g1", mode="review")
    assert states("g1")["refinement"] == "needs_review" and session.load("g1")["status"] == "refinement_ready" and [c[0] for c in fake.calls] == ["names", "refine"]
    g.approve("g1", "refinement")
    g.run("g1", mode="review")
    s = session.load("g1")
    assert states("g1")["report"] == "completed" and s["status"] == "report_ready" and s["awaiting"] is None
    assert [c[0] for c in fake.calls][-2:] == ["extract", "review"] or [c[0] for c in fake.calls][-1] == "extract"


def test_auto_mode_runs_straight_through(wav, monkeypatch):
    start(wav, monkeypatch, job="g2", mode="auto")
    out = g.run("g2", str(wav), mode="auto")
    assert out["outcome"] == "ok" and set(states("g2").values()) == {"completed"} and session.load("g2")["status"] == "report_ready"
    assert [t.split(":")[0] for t in out["trace"]] == ["transcription", "speakers", "refinement", "report"]  # every transition is traceable


def test_a_stage_with_nothing_to_review_does_not_stop_the_run(wav, monkeypatch):
    fake = start(wav, monkeypatch, job="g3")
    fake.r_refine = lambda user: {"edits": []}
    monkeypatch.setattr(settings, "names_enabled", False)
    g.run("g3", str(wav), mode="review")
    g.approve("g3", "speakers")
    g.run("g3", mode="review")
    assert states("g3")["refinement"] == "completed" and states("g3")["report"] == "completed"
    assert session.load("g3")["stages"]["refinement"]["message"] == "No corrections were needed"


def test_a_failed_stage_is_recorded_in_plain_words_and_earlier_work_is_kept(wav, monkeypatch):
    fake = start(wav, monkeypatch, job="g4", mode="auto")
    fake.fail = {"refine"}
    fake.r_refine = None
    g.run("g4", str(wav), mode="auto")  # refinement failures are tolerated by the step, so make the report step fail instead
    fake.fail = {"extract"}
    session.reset_from("g4", "report")
    g.run("g4", mode="auto")
    s = session.load("g4")
    assert s["status"] == "failed" and s["stages"]["report"]["state"] == "failed" and s["stages"]["transcription"]["state"] == "completed"
    err = s["stages"]["report"]["error"]
    assert err["code"] == "ANALYSIS_FAILED" and err["title"] and err["action"] and "Traceback" not in str(s)
    assert load_phase2("g4") is not None  # the transcript and the speaker/refinement results are still there


def test_a_failed_stage_can_be_retried_without_redoing_earlier_stages(wav, monkeypatch):
    fake = start(wav, monkeypatch, job="g5", mode="auto")
    fake.fail = {"extract"}
    g.run("g5", str(wav), mode="auto")
    assert session.load("g5")["stages"]["report"]["state"] == "failed"
    n_before = [c[0] for c in fake.calls].count("names")
    fake.fail = set()
    g.retry("g5", "report")
    g.run("g5", mode="auto")
    assert session.load("g5")["status"] == "report_ready" and [c[0] for c in fake.calls].count("names") == n_before  # only the report step ran again
    assert session.load("g5")["stages"]["report"]["attempts"] == 2


def test_an_unreadable_file_fails_the_audio_stage_with_a_clear_message(tmp_path, monkeypatch):
    install(monkeypatch)
    bad = tmp_path / "empty.wav"
    bad.write_bytes(b"")
    session.create("g6", "empty.wav", None, "review")
    g.run("g6", str(bad), mode="review")
    s = session.load("g6")
    assert s["stages"]["audio"]["state"] == "failed" and s["stages"]["transcription"]["state"] == "waiting" and s["status"] == "failed"
    assert s["stages"]["audio"]["error"]["code"] == "EMPTY_FILE" and "empty" in s["stages"]["audio"]["error"]["title"].lower()


def test_approving_a_stage_that_is_not_waiting_is_refused(wav, monkeypatch):
    start(wav, monkeypatch, job="g7")
    with pytest.raises(PipelineError):
        g.approve("g7", "speakers")


def test_every_error_code_has_a_title_and_an_action_and_unknown_ones_fall_back_safely():
    from app.core.errors import CATALOG

    assert all(t and a for t, a in CATALOG.values())
    d = describe(PipelineError("SOMETHING_NEW", "odd"))
    assert d["code"] == "SOMETHING_NEW" and d["title"] and d["action"] and describe(None) is None
    assert "Traceback" not in str(describe(ValueError("boom")))
    assert "." in describe(PipelineError("UNSUPPORTED_FORMAT", "Files of type .txt are not supported."))["message"]


def test_a_meeting_from_before_sessions_existed_is_read_from_its_files(wav, monkeypatch):
    from app.core.pipeline import run_phase1

    install(monkeypatch)
    run_phase1(wav, job_id="old1")
    s = session.derive("old1")
    assert s["stages"]["transcription"]["state"] == "completed" and s["stages"]["report"]["state"] == "waiting" and session.overall(s) == "speakers_ready" or session.overall(s) == "transcript_ready"

