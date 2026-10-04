"""Deepgram nova-3: transcript + word confidence + speaker diarization, whole file in one request."""
import mimetypes
from pathlib import Path

import numpy as np

from app.asr.base import ASRProvider, ASRResult, ProgressFn
from app.asr.http import post_with_retry
from app.config.settings import settings
from app.schemas.transcript import Transcript, TranscriptSegment, Word
from app.utils.errors import PipelineError

URL = "https://api.deepgram.com/v1/listen"


class DeepgramProvider(ASRProvider):
    name = "deepgram"
    supports_diarization = True

    def __init__(self, model: str | None = None):
        if not settings.deepgram_api_key:
            raise PipelineError("API_KEY_MISSING", "DEEPGRAM_API_KEY is not set. Add it to the .env file.")
        self.model = model or settings.asr_model or settings.deepgram_default_model

    def transcribe(self, path: Path, samples: np.ndarray, sample_rate: int, progress: ProgressFn | None = None) -> ASRResult:
        if progress:
            progress("Transcribing with speaker detection")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        resp = post_with_retry(
            URL,
            provider="Deepgram",
            params={
                "model": self.model,
                "language": "en",
                "smart_format": "true",
                "punctuate": "true",
                "diarize": "true",
                "utterances": "true",
            },
            headers={"Authorization": f"Token {settings.deepgram_api_key}", "Content-Type": ctype},
            content=path.read_bytes(),
        )
        data = resp.json()
        segments = _parse(data)
        tr = Transcript(
            provider=self.name,
            model=self.model,
            kind="raw",
            duration_seconds=round(len(samples) / sample_rate, 3),
            speakers_known=True,
            segments=segments,
        )
        return ASRResult(tr, {"response": data}, chunks=0)


def _speaker(n) -> str:
    return f"SPEAKER_{int(n):02d}" if n is not None else "SPEAKER_UNKNOWN"


def _parse(data: dict) -> list[TranscriptSegment]:
    results = data.get("results", {})
    out: list[TranscriptSegment] = []
    for u in results.get("utterances") or []:
        text = (u.get("transcript") or "").strip()
        if not text:
            continue
        words = [
            Word(
                text=(w.get("punctuated_word") or w["word"]),
                start=w["start"],
                end=w["end"],
                confidence=w.get("confidence"),
                speaker=_speaker(w.get("speaker")),
            )
            for w in u.get("words", [])
        ]
        spk = u.get("speaker")
        out.append(
            TranscriptSegment(
                segment_id=f"seg_{len(out) + 1:04d}",
                speaker=_speaker(spk),
                speaker_known=spk is not None,
                start=u["start"],
                end=u["end"],
                text=text,
                words=words,
                confidence=u.get("confidence"),
                language="en",
            )
        )
    return out
