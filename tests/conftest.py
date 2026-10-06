import numpy as np
import pytest
import soundfile as sf

from app.config.settings import settings


@pytest.fixture(autouse=True)
def tmp_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "cache_enabled", False)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "deepgram_api_key", "test-key")
    monkeypatch.setattr(settings, "candidate_detector", "off")  # no real LLM calls in tests
    monkeypatch.setattr(settings, "refine_use_second_asr", False)
    import app.minutes.llm as _mllm
    from app.utils.errors import PipelineError as _PE

    def _no_network(*a, **k):
        raise _PE("API_ERROR", "LLM calls are disabled in tests unless a test installs a fake")

    monkeypatch.setattr(_mllm, "_call_model", _no_network)
    import app.refinement.speakers as _spk
    monkeypatch.setattr(_spk, "chat_json", lambda *a, **k: {"speakers": []})  # never call a real LLM from tests


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
