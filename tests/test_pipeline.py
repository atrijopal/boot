import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.asr import http as asr_http
from app.asr.groq_provider import _parse_chunk
from app.config.settings import settings
from app.preprocessing.normalize import normalize_transcript
from app.preprocessing.pipeline import run_phase1
from app.schemas.transcript import Transcript, TranscriptSegment
from app.utils.errors import PipelineError

GROQ_RESPONSE = {
    "language": "english",
    "segments": [
        {"id": 0, "start": 0.0, "end": 4.0, "text": " We don't need 81.73 percent accuracy.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
        {"id": 1, "start": 4.0, "end": 8.0, "text": " I'll prepare the NAFNet report by Friday.", "avg_logprob": -0.3, "no_speech_prob": 0.01},
        {"id": 2, "start": 8.0, "end": 9.0, "text": " Thank you.", "avg_logprob": -1.6, "no_speech_prob": 0.95},
    ],
    "words": [{"word": "We", "start": 0.0, "end": 0.3}, {"word": "don't", "start": 0.3, "end": 0.6}, {"word": "I'll", "start": 4.1, "end": 4.4}],
}


class FakeResp:
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_groq_parsing_offsets_and_words():
    segs = _parse_chunk(GROQ_RESPONSE, offset=600.0, id_base=10)
    assert segs[0].segment_id == "seg_0011" and segs[0].start == 600.0 and segs[1].start == 604.0
    assert [w.text for w in segs[0].words] == ["We", "don't"] and segs[0].words[0].start == 600.0
    assert segs[1].words[0].text == "I'll"
    assert all(s.speaker == "SPEAKER_UNKNOWN" and not s.speaker_known for s in segs)


def test_normalize_keeps_raw_and_drops_hallucination():
    raw = Transcript(provider="t", model="t", segments=[
        TranscriptSegment(segment_id="seg_0001", start=0, end=2, text="  We  don't   need it . ", avg_logprob=-0.2, no_speech_prob=0.0),
        TranscriptSegment(segment_id="seg_0002", start=2.1, end=3, text="We don't need it.", avg_logprob=-0.2, no_speech_prob=0.0),
        TranscriptSegment(segment_id="seg_0003", start=3, end=4, text="Thank you.", avg_logprob=-1.6, no_speech_prob=0.95),
    ])
    n = normalize_transcript(raw)
    assert [s.text for s in n.segments] == ["We don't need it."]
    assert raw.segments[0].text == "  We  don't   need it . "  # raw untouched
    assert len(raw.segments) == 3
    assert any("seg_0002" in x for x in n.notes) and any("seg_0003" in x for x in n.notes)


def test_end_to_end_with_mocked_api(speech_like_wav, monkeypatch):
    calls = []
    monkeypatch.setattr(asr_http, "post_with_retry", lambda *a, **k: calls.append(1) or FakeResp(GROQ_RESPONSE))
    import app.asr.groq_provider as gp
    monkeypatch.setattr(gp, "post_with_retry", asr_http.post_with_retry)
    res = run_phase1(speech_like_wav, job_id="job1")
    assert res.status == "success" and len(calls) == 1
    assert len(res.raw_transcript.segments) == 3 and len(res.normalized_transcript.segments) == 2
    assert {n.value for n in res.protected_facts.numbers} == {"81.73"}
    assert res.protected_facts.negations
    assert res.protected_facts.dates[0].surface == "Friday"
    out = settings.outputs_dir + "/job1"
    for name in ("raw_transcript.json", "raw_transcript.txt", "normalized_transcript.json", "protected_facts.json", "meeting_context.json", "asr_raw.json", "phase1_result.json"):
        assert (__import__("pathlib").Path(out) / name).is_file(), name
    assert json.loads((__import__("pathlib").Path(out) / "asr_raw.json").read_text())["chunks"][0]["response"] == GROQ_RESPONSE


def test_cache_prevents_second_api_call(speech_like_wav, monkeypatch):
    monkeypatch.setattr(settings, "cache_enabled", True)
    calls = []
    import app.asr.groq_provider as gp
    monkeypatch.setattr(gp, "post_with_retry", lambda *a, **k: calls.append(1) or FakeResp(GROQ_RESPONSE))
    run_phase1(speech_like_wav, job_id="a")
    second = run_phase1(speech_like_wav, job_id="b")
    assert len(calls) == 1 and second.cache_hit


def test_empty_asr_result_is_a_clear_error(speech_like_wav, monkeypatch):
    import app.asr.groq_provider as gp
    monkeypatch.setattr(gp, "post_with_retry", lambda *a, **k: FakeResp({"segments": [], "words": []}))
    with pytest.raises(PipelineError) as e:
        run_phase1(speech_like_wav)
    assert e.value.code == "NO_SPEECH"


def test_missing_key_is_a_clear_error(speech_like_wav, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(PipelineError) as e:
        run_phase1(speech_like_wav)
    assert e.value.code == "API_KEY_MISSING" and "GROQ_API_KEY" in e.value.message


def test_http_retry_then_rate_limit_error(monkeypatch):
    class R:
        status_code, headers, text = 429, {"retry-after": "0"}, ""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(PipelineError) as e:
        asr_http.post_with_retry("http://x", provider="Groq", retries=3)
    assert e.value.code == "API_RATE_LIMITED"


def test_api_rejects_bad_uploads_cleanly(tmp_path):
    import time
    client = TestClient(app)
    for name, data, code in [("empty.wav", b"", "EMPTY_FILE"), ("x.txt", b"hello", "UNSUPPORTED_FORMAT"), ("fake.mp3", b"not audio at all....", "UNSUPPORTED_FORMAT")]:
        r = client.post("/process-audio", files={"file": (name, data)})
        job = r.json()["job_id"]
        for _ in range(50):
            j = client.get(f"/jobs/{job}").json()
            if j["stage"] in ("failed", "done"):
                break
            time.sleep(0.1)
        assert j["stage"] == "failed" and j["error"]["code"] == code, (name, j)
        assert "Traceback" not in json.dumps(j)
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/health").json()["status"] == "ok"


def test_groq_request_is_valid_multipart(monkeypatch):
    """Builds the real httpx request (no network): catches form-encoding mistakes that mocks hide."""
    import app.asr.groq_provider as gp
    seen = {}

    def fake_post(url, *, provider, **kw):
        req = httpx.Client().build_request("POST", url, files=kw["files"], data=kw["data"])
        seen["body"] = req.read()
        return FakeResp({"segments": [], "words": []})

    monkeypatch.setattr(gp, "post_with_retry", fake_post)
    gp.GroqProvider()._call(b"flac-bytes", "whisper-large-v3")
    body = seen["body"]
    assert body.count(b'name="timestamp_granularities[]"') == 2
    assert b"verbose_json" in body and b"flac-bytes" in body
