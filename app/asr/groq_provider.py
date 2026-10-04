"""Groq-hosted Whisper. Text + word timestamps; no speaker labels."""
from pathlib import Path

import numpy as np

from app.asr.base import ASRProvider, ASRResult, ProgressFn
from app.asr.http import post_with_retry
from app.audio.chunk import split_chunks
from app.config.settings import settings
from app.schemas.transcript import Transcript, TranscriptSegment, Word
from app.utils.errors import PipelineError

URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqProvider(ASRProvider):
    name = "groq"

    def __init__(self, model: str | None = None):
        if not settings.groq_api_key:
            raise PipelineError("API_KEY_MISSING", "GROQ_API_KEY is not set. Add it to the .env file.")
        self.model = model or settings.asr_model or settings.groq_default_model

    def _call(self, flac: bytes, model: str) -> dict:
        resp = post_with_retry(
            URL,
            provider="Groq",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            files={"file": ("chunk.flac", flac, "audio/flac")},
            data={
                "model": model,
                "response_format": "verbose_json",
                "language": "en",
                "temperature": "0",
                "timestamp_granularities[]": ["word", "segment"],
            },
        )
        return resp.json()

    def transcribe(self, path: Path, samples: np.ndarray, sample_rate: int, progress: ProgressFn | None = None) -> ASRResult:
        chunks = split_chunks(samples, sample_rate)
        raw: dict = {"chunks": []}
        segments: list[TranscriptSegment] = []
        notes: list[str] = []
        model_used = self.model
        for c in chunks:
            if progress:
                progress(f"Transcribing chunk {c.index + 1}/{len(chunks)}")
            try:
                data = self._call(c.flac, self.model)
            except PipelineError as e:
                if e.code not in ("API_RATE_LIMITED", "API_UNAVAILABLE") or not settings.groq_fallback_model:
                    raise
                notes.append(f"{self.model} was rate limited; chunk {c.index} used {settings.groq_fallback_model}.")
                data = self._call(c.flac, settings.groq_fallback_model)
                model_used = f"{self.model}+{settings.groq_fallback_model}"
            raw["chunks"].append({"index": c.index, "offset": c.offset, "response": data})
            segments.extend(_parse_chunk(data, c.offset, len(segments)))

        tr = Transcript(
            provider=self.name,
            model=model_used,
            kind="raw",
            duration_seconds=round(len(samples) / sample_rate, 3),
            speakers_known=False,
            segments=segments,
            notes=notes,
        )
        return ASRResult(tr, raw, chunks=len(chunks))


def _parse_chunk(data: dict, offset: float, id_base: int) -> list[TranscriptSegment]:
    words = [
        Word(text=w["word"].strip(), start=round(offset + w["start"], 3), end=round(offset + w["end"], 3))
        for w in data.get("words", []) or []
    ]
    out: list[TranscriptSegment] = []
    wi = 0
    for s in data.get("segments", []) or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start, end = offset + s["start"], offset + s["end"]
        seg_words: list[Word] = []
        # words and segments both arrive time-ordered: walk one pointer through the words
        while wi < len(words) and words[wi].start < end - 0.02:
            if words[wi].start >= start - 0.05:
                seg_words.append(words[wi])
            wi += 1
        out.append(
            TranscriptSegment(
                segment_id=f"seg_{id_base + len(out) + 1:04d}",
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                words=seg_words,
                avg_logprob=s.get("avg_logprob"),
                no_speech_prob=s.get("no_speech_prob"),
                language=data.get("language"),
            )
        )
    return out
