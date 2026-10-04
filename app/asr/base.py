from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.schemas.transcript import Transcript

ProgressFn = Callable[[str], None]


@dataclass
class ASRResult:
    transcript: Transcript
    raw: dict  # provider responses, verbatim, for debugging
    chunks: int = 0


class ASRProvider(ABC):
    name: str
    model: str
    supports_diarization: bool = False

    @abstractmethod
    def transcribe(
        self, path: Path, samples: np.ndarray, sample_rate: int, progress: ProgressFn | None = None
    ) -> ASRResult:
        """Return a raw transcript with global (whole-recording) timestamps."""
