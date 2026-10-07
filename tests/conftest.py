import numpy as np
import pytest
import soundfile as sf

import app.core.llm as llm
from app.config.settings import settings
from app.utils.errors import PipelineError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "outputs_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "cache_enabled", False)
    monkeypatch.setattr(settings, "extraction_lenses", 1)  # the focused readers have their own tests; the others count calls with one combined reader
    monkeypatch.setattr(settings, "verify_edits_enabled", False)  # the second check of fixes has its own tests
    monkeypatch.setattr(settings, "fallback_model", "")  # a developer's own .env must not change what the tests see
    monkeypatch.setattr(settings, "names_enabled", False)  # the who-is-who call has its own tests; the others count calls without it
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "deepgram_api_key", "test-key")

    def no_network(*a, **k):
        raise PipelineError("API_ERROR", "model calls are disabled in tests unless a test installs a fake")

    monkeypatch.setattr(llm, "_call_model", no_network)


@pytest.fixture
def wav(tmp_path):
    """A file with signal (noise bursts and pauses). The ASR is faked in tests, so the audio content does not matter."""
    sr, rng = 16000, np.random.default_rng(0)
    parts = []
    for _ in range(6):
        parts += [rng.normal(0, 0.1, sr * 2), np.zeros(sr)]
    p = tmp_path / "meeting.wav"
    sf.write(p, np.concatenate(parts).astype(np.float32), sr)
    return p
