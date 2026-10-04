from pydantic import BaseModel


class AudioMetadata(BaseModel):
    original_file: str
    format: str
    file_size_bytes: int
    sha256: str
    duration_seconds: float
    original_sample_rate: int
    original_channels: int
    processed_sample_rate: int
    processed_channels: int
    rms: float
    peak: float
    clipped_fraction: float
    source_path: str = ""  # where the audio was read from (needed by Phase 2's second-ASR check)
    chunks: int = 0  # number of ASR chunks (0 if the provider takes the whole file)
