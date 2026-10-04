"""Step 1: file validation. Fails with clear, user-safe messages."""
import hashlib
from pathlib import Path

from app.config.settings import settings
from app.utils.errors import PipelineError

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".oga", ".opus", ".webm", ".aac"}


def _magic_ok(head: bytes) -> bool:
    return (
        (head[:4] == b"RIFF" and head[8:12] == b"WAVE")  # wav
        or head[:3] == b"ID3"  # mp3 with tag
        or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)  # mp3 / aac frame sync
        or head[4:8] == b"ftyp"  # m4a / mp4
        or head[:4] == b"fLaC"
        or head[:4] == b"OggS"
        or head[:4] == b"\x1a\x45\xdf\xa3"  # webm / matroska
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate_file(path: str | Path) -> Path:
    """Cheap checks that do not need decoding. Raises PipelineError."""
    p = Path(path)
    if not p.is_file():
        raise PipelineError("FILE_MISSING", "The uploaded file could not be found.")
    size = p.stat().st_size
    if size == 0:
        raise PipelineError("EMPTY_FILE", "The file is empty (0 bytes). Please upload a valid audio recording.")
    if size > settings.max_upload_mb * 1024 * 1024:
        raise PipelineError("FILE_TOO_LARGE", f"The file is larger than the {settings.max_upload_mb} MB limit.")
    ext = p.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise PipelineError(
            "UNSUPPORTED_FORMAT",
            f"Unsupported file type '{ext or 'none'}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}.",
        )
    with open(p, "rb") as f:
        head = f.read(16)
    if not _magic_ok(head):
        raise PipelineError(
            "UNSUPPORTED_FORMAT",
            "The file content does not look like audio, even though the extension says it is. It may be corrupted or mislabeled.",
        )
    return p
