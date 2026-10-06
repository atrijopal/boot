import json

import pytest

from app.config.settings import settings
from app.summarization import llm
from app.summarization.pipeline import latest_folder, load_result, run_phase3
from app.summarization.summaries import registers
from app.utils.errors import PipelineError


def test_full_pipeline_preserves_proposal_revision_owner_and_deadline(phase3_job):
    job_id, root, fake = phase3_job
    before = (root / "refinement.json").read_bytes()
    result = run_phase3(job_id)
    assert result.manifest.status == "complete"
    assert (root / "refinement.json").read_bytes() == before
    assert len(result.topics) == 2
    assert next(e for e in result.events if "20,000" in e.description).kind == "proposal"
    decisions = registers(result.events)["decisions"]
    assert [e.status for e in decisions] == ["superseded", "confirmed"]
    assert result.relations[0].kind == "SUPERSEDES"
    action = registers(result.events)["action_items"][0]
    assert action.owner_speaker_id == "S3" and action.owner_text is None
    assert action.deadline_text == "by next Friday"
    assert result.validation_report["structural_checks_passed"] is True
    folder = latest_folder(job_id)
    for name in ("manifest", "units", "topics", "topic_assignments", "events", "knowledge_graph", "topic_summaries", "decisions", "action_items", "open_questions", "minutes", "validation_report"):
        assert (folder / f"{name}.json").is_file()
    assert "Not stated" not in (folder / "minutes.md").read_text(encoding="utf-8").split("## Action items")[1].split("## Open questions")[0]


def test_verifier_rejection_excludes_events_from_confirmed_registers(phase3_job):
    job_id, root, fake = phase3_job
    fake.false_event = True
    result = run_phase3(job_id)
    assert all(e.validation_status == "needs_review" for e in result.events)
    assert registers(result.events)["decisions"] == []
    assert result.executive_summary == []


def test_unsupported_summary_falls_back_to_supported_events(phase3_job):
    job_id, root, fake = phase3_job
    fake.bad_summary = True
    result = run_phase3(job_id)
    assert result.topic_summaries[0].claims
    assert all(c.text.startswith("[") for t in result.topic_summaries for c in t.claims)
    assert any("rejected by the verifier" in w for w in result.manifest.warnings)


def test_api_failure_is_partial_and_resume_uses_successful_checkpoints(phase3_job, monkeypatch):
    job_id, root, fake = phase3_job
    monkeypatch.setattr(settings, "phase3_batch_chars", 6000)
    source = json.loads((root / "refinement.json").read_text())
    for segment in source["refined_transcript"]["segments"]:
        segment["speaker_name"] = "Participant name from the synthetic meeting fixture"
    (root / "refinement.json").write_text(json.dumps(source))
    fake.fail_anchor = "u000009"
    result = run_phase3(job_id)
    assert result.manifest.status == "partial" and result.manifest.failed_batches
    successful_calls = [x for x in fake.calls if "target" in x and "u000009" not in {r["unit_id"] for r in x["target"]}]
    assert successful_calls
    fake.calls.clear()
    fake.fail_anchor = None
    result = run_phase3(job_id)
    assert result.manifest.status == "complete"
    assert not any(x in successful_calls for x in fake.calls)
    assert set(result.manifest.covered_unit_ids) == {u.unit_id for u in result.units}


def test_changed_transcript_blocks_saved_outputs(phase3_job):
    job_id, root, fake = phase3_job
    run_phase3(job_id)
    value = json.loads((root / "refinement.json").read_text())
    value["refined_transcript"]["segments"][0]["speaker_name"] = "New accepted name"
    (root / "refinement.json").write_text(json.dumps(value))
    with pytest.raises(PipelineError) as error:
        load_result(job_id)
    assert error.value.code == "PHASE3_STALE"


def test_edit_during_run_never_publishes_a_stale_result(phase3_job):
    job_id, root, fake = phase3_job
    def change(stage, message):
        if stage == "summaries":
            data = json.loads((root / "refinement.json").read_text())
            data["manual_names"]["S1"] = "Changed"
            (root / "refinement.json").write_text(json.dumps(data))
    with pytest.raises(PipelineError) as error:
        run_phase3(job_id, status=change)
    assert error.value.code == "PHASE3_STALE"
    assert not (root / "phase3" / "latest.json").exists()


def test_settings_and_prompt_versions_change_run_identity(phase3_job, monkeypatch):
    job_id, root, fake = phase3_job
    first = run_phase3(job_id)
    monkeypatch.setattr(settings, "phase3_temperature", .2)
    second = run_phase3(job_id)
    assert first.manifest.run_id != second.manifest.run_id
    assert first.manifest.source_revision == second.manifest.source_revision


def test_no_api_secrets_in_manifest(phase3_job):
    result = run_phase3(phase3_job[0])
    text = result.manifest.model_dump_json()
    assert settings.groq_api_key not in text and "api_key" not in text


def test_nonboolean_verdict_does_not_pass(phase3_job, monkeypatch):
    job_id, root, fake = phase3_job
    def model(system, user, **kwargs):
        response = fake(system, user, **kwargs)
        if json.loads(user).get("task") == "events":
            for row in response["verdicts"]:
                row["status_supported"] = "true"
        return response
    monkeypatch.setattr(llm, "chat_json", model)
    result = run_phase3(job_id)
    assert all(e.validation_status == "needs_review" for e in result.events)

