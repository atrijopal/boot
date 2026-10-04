"""Step 2: decode to mono 16 kHz float32. The original file is only read, never modified."""
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

from app.audio.validate import sha256_file
from app.config.settings import settings
from app.schemas.audio import AudioMetadata
from app.utils.errors import PipelineError

TARGET_SR = 16000


@dataclass
class PreparedAudio:
    samples: np.ndarray  # mono float32 @ TARGET_SR
    sample_rate: int
    metadata: AudioMetadata


def prepare_audio(path: Path) -> PreparedAudio:
    try:
        container = av.open(str(path))
    except Exception:
        raise PipelineError("UNREADABLE_AUDIO", "The audio file could not be opened. It may be corrupted or not real audio.") from None

    try:
        streams = container.streams.audio
        if not streams:
            raise PipelineError("NO_AUDIO_STREAM", "The file does not contain an audio track.")
        stream = streams[0]
        orig_sr = int(stream.rate or 0)
        orig_ch = int(stream.channels or 0)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=TARGET_SR)
        parts: list[np.ndarray] = []
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    parts.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                parts.append(out.to_ndarray().reshape(-1))
        except PipelineError:
            raise
        except Exception:
            if not parts:
                raise PipelineError("UNREADABLE_AUDIO", "The audio could not be decoded. The file looks corrupted.") from None
            # Truncated file: keep what decoded fine rather than failing the whole run.
    finally:
        container.close()

    if not parts:
        raise PipelineError("UNREADABLE_AUDIO", "No audio could be decoded from this file.")
    samples = np.concatenate(parts).astype(np.float32)
    duration = len(samples) / TARGET_SR

    if duration < settings.min_duration_seconds:
        raise PipelineError("TOO_SHORT", f"The recording is too short ({duration:.1f}s) to transcribe.")
    if duration > settings.max_duration_seconds:
        raise PipelineError("TOO_LONG", f"The recording is longer than the {settings.max_duration_seconds / 3600:.0f} h limit.")

    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples)))
    if rms < settings.min_rms:
        raise PipelineError("SILENT_AUDIO", "The recording contains no usable audio signal (it is silent).")

    meta = AudioMetadata(
        original_file=path.name,
        source_path=str(path.resolve()),
        format=path.suffix.lower().lstrip("."),
        file_size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        duration_seconds=round(duration, 3),
        original_sample_rate=orig_sr,
        original_channels=orig_ch,
        processed_sample_rate=TARGET_SR,
        processed_channels=1,
        rms=round(rms, 6),
        peak=round(peak, 4),
        clipped_fraction=round(float(np.mean(np.abs(samples) >= 0.999)), 6),
    )
    return PreparedAudio(samples=samples, sample_rate=TARGET_SR, metadata=meta)
