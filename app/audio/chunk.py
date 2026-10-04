"""Step 3: silence-aware chunking so each API upload stays under the provider size limit."""
import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from app.config.settings import settings


@dataclass
class AudioChunk:
    index: int
    offset: float  # seconds from the start of the recording
    duration: float
    flac: bytes


def _quietest_cut(samples: np.ndarray, sr: int, lo: int, hi: int, frame_s: float = 0.1) -> int:
    """Sample index in [lo, hi) with the lowest short-time energy (a pause between words)."""
    frame = int(sr * frame_s)
    if hi - lo < 2 * frame:
        return hi
    n = (hi - lo) // frame
    seg = samples[lo : lo + n * frame].reshape(n, frame)
    energy = np.sqrt((seg**2).mean(axis=1))
    return lo + int(np.argmin(energy)) * frame + frame // 2


def _encode(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def split_chunks(samples: np.ndarray, sr: int, max_seconds: int | None = None) -> list[AudioChunk]:
    """Split into chunks of at most `max_seconds`, cutting at the quietest point in the last 60 s.

    If an encoded chunk is still above the size limit, retry with a smaller chunk length.
    """
    max_seconds = max_seconds or settings.chunk_seconds
    while True:
        chunks = _split(samples, sr, max_seconds)
        if max(len(c.flac) for c in chunks) <= settings.max_chunk_bytes or max_seconds <= 30:
            return chunks
        max_seconds = max(30, max_seconds // 2)


def _split(samples: np.ndarray, sr: int, max_seconds: int) -> list[AudioChunk]:
    total = len(samples)
    max_len = int(max_seconds * sr)
    window = min(int(60 * sr), max_len // 2)
    chunks: list[AudioChunk] = []
    pos = 0
    while pos < total:
        if total - pos <= max_len:
            end = total
        else:
            end = _quietest_cut(samples, sr, pos + max_len - window, pos + max_len)
        piece = samples[pos:end]
        chunks.append(AudioChunk(len(chunks), pos / sr, len(piece) / sr, _encode(piece, sr)))
        pos = end
    return chunks
