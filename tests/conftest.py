import numpy as np
import pytest
import soundfile as sf
import httpx

from app.config.settings import settings


@pytest.fixture(autouse=True)
def tmp_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "cache_enabled", False)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "deepgram_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "candidate_detector", "off")  # no real LLM calls in tests
    monkeypatch.setattr(settings, "refine_use_second_asr", False)
    import app.refinement.speakers as _spk
    monkeypatch.setattr(_spk, "chat_json", lambda *a, **k: {"speakers": []})  # never call a real LLM from tests


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is disabled in tests; mock the provider response.")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def speech_like_wav(tmp_path):
    """Noise bursts with pauses: has signal, not real speech (ASR is mocked in tests)."""
    sr, rng = 16000, np.random.default_rng(0)
    parts = []
    for _ in range(6):
        parts += [rng.normal(0, 0.1, sr * 2), np.zeros(sr)]
    p = tmp_path / "meeting.wav"
    sf.write(p, np.concatenate(parts).astype(np.float32), sr)
    return p
