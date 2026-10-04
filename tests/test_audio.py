import numpy as np
import pytest
import soundfile as sf

from app.audio.chunk import split_chunks
from app.audio.prepare import prepare_audio
from app.audio.validate import validate_file
from app.utils.errors import PipelineError


def code_of(fn, *a):
    with pytest.raises(PipelineError) as e:
        fn(*a)
    return e.value.code


def test_valid_wav(speech_like_wav):
    p = validate_file(speech_like_wav)
    prepared = prepare_audio(p)
    assert prepared.metadata.processed_sample_rate == 16000
    assert prepared.metadata.processed_channels == 1
    assert abs(prepared.metadata.duration_seconds - 18) < 0.1
    assert len(prepared.metadata.sha256) == 64


def test_original_untouched(speech_like_wav):
    before = speech_like_wav.read_bytes()
    prepare_audio(validate_file(speech_like_wav))
    assert speech_like_wav.read_bytes() == before


def test_empty_file(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"")
    assert code_of(validate_file, f) == "EMPTY_FILE"


def test_unsupported_extension(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert code_of(validate_file, f) == "UNSUPPORTED_FORMAT"


def test_fake_audio_content(tmp_path):
    f = tmp_path / "fake.mp3"
    f.write_bytes(b"this is not audio at all, just text")
    assert code_of(validate_file, f) == "UNSUPPORTED_FORMAT"


def test_corrupted_audio(tmp_path):
    f = tmp_path / "bad.wav"
    f.write_bytes(b"RIFF\x00\x00\x00\x00WAVEgarbagegarbagegarbagegarbage")
    p = validate_file(f)
    assert code_of(prepare_audio, p) in ("UNREADABLE_AUDIO", "NO_AUDIO_STREAM")


def test_silent_audio(tmp_path):
    f = tmp_path / "silent.wav"
    sf.write(f, np.zeros(16000 * 3, dtype=np.float32), 16000)
    assert code_of(prepare_audio, validate_file(f)) == "SILENT_AUDIO"


def test_too_short(tmp_path):
    f = tmp_path / "short.wav"
    sf.write(f, (np.random.default_rng(1).normal(0, 0.1, 4000)).astype(np.float32), 16000)
    assert code_of(prepare_audio, validate_file(f)) == "TOO_SHORT"


def test_stereo_resampled_to_mono_16k(tmp_path):
    f = tmp_path / "st.wav"
    sf.write(f, np.random.default_rng(2).normal(0, 0.1, (44100 * 3, 2)).astype(np.float32), 44100)
    m = prepare_audio(validate_file(f)).metadata
    assert (m.original_sample_rate, m.original_channels, m.processed_sample_rate, m.processed_channels) == (44100, 2, 16000, 1)


def test_chunking_cuts_at_pauses_and_covers_everything():
    sr = 16000
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.1, sr * 100).astype(np.float32)
    x[sr * 28 : sr * 29] = 0  # a pause near the 30 s limit
    chunks = split_chunks(x, sr, max_seconds=30)
    assert len(chunks) >= 4
    assert abs(sum(c.duration for c in chunks) - 100) < 0.01
    assert all(c.duration <= 30.01 for c in chunks)
    assert abs(chunks[1].offset - chunks[0].duration) < 1e-6
    assert 28 <= chunks[0].duration <= 29.01  # cut inside the pause
