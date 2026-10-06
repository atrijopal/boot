import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.api.main import app, jobs
from app.config.settings import ROOT, Settings, settings
from app.llm.client import chat_api_key
from app.refinement.pipeline import job_dir
from app.utils.errors import PipelineError


def test_example_contains_no_credentials():
    values = dotenv_values(ROOT / ".env.example")
    for key in ("GROQ_API_KEY", "DEEPGRAM_API_KEY", "LLM_API_KEY"):
        assert values[key] == ""


def test_environment_overrides_utf8_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=file-placeholder\nOUTPUTS_DIR=résultats\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "environment-placeholder")
    monkeypatch.delenv("OUTPUTS_DIR", raising=False)
    config = Settings(_env_file=env)
    assert config.groq_api_key == "environment-placeholder"
    assert config.outputs_dir == "résultats"
    assert "environment-placeholder" not in repr(config)
    assert "api_key" not in config.model_dump_json()


def test_custom_chat_endpoint_requires_separate_key(monkeypatch):
    assert chat_api_key() == "test-key"
    monkeypatch.setattr(settings, "llm_base_url", "https://example.invalid/v1")
    with pytest.raises(PipelineError, match="LLM_API_KEY"):
        chat_api_key()
    monkeypatch.setattr(settings, "llm_api_key", "chat-placeholder")
    assert chat_api_key() == "chat-placeholder"


@pytest.mark.parametrize("identifier", ["..", "../outside", "C:\\outside", "/tmp/outside", ""])
def test_job_paths_cannot_escape_outputs(identifier):
    with pytest.raises(PipelineError) as err:
        job_dir(identifier)
    assert err.value.code == "BAD_JOB_ID"


def test_job_status_restores_saved_result_after_restart(tmp_path):
    folder = job_dir("saved_job")
    folder.mkdir(parents=True)
    (folder / "phase1_result.json").write_text('{"job_id":"saved_job"}', encoding="utf-8")
    jobs.pop("saved_job", None)
    client = TestClient(app)
    response = client.get("/jobs/saved_job")
    assert response.status_code == 200
    assert response.json()["stage"] == "done"
    assert client.get("/jobs/%5Coutside/download").status_code == 400
    jobs.pop("saved_job", None)


def test_check_requires_selected_deepgram_key(monkeypatch):
    from scripts.check_keys import check_deepgram
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    monkeypatch.setattr(settings, "asr_provider", "deepgram")
    assert check_deepgram() is False
    monkeypatch.setattr(settings, "asr_provider", "groq")
    assert check_deepgram() is True


def test_check_includes_phase3_models(monkeypatch):
    from scripts import check_keys
    monkeypatch.setattr(settings, "phase3_model", "custom-extractor")
    monkeypatch.setattr(settings, "phase3_verify_model", "custom-verifier")
    observed = {}
    def fake_models(label, endpoint, key, needed):
        observed["needed"] = needed
        return True
    monkeypatch.setattr(check_keys, "check_models", fake_models)
    assert check_keys.check_llm() is True
    assert {"custom-extractor", "custom-verifier"} <= observed["needed"]
