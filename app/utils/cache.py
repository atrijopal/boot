"""ASR result cache: same audio + provider + model + settings => no new API call."""
import hashlib
import json
from pathlib import Path

from app.config.settings import settings


def _key(sha: str, provider: str, model: str, extra: str = "") -> str:
    return hashlib.sha256(f"{sha}|{provider}|{model}|{extra}".encode()).hexdigest()[:32]


def _path(sha: str, provider: str, model: str) -> Path:
    extra = f"chunk={settings.chunk_seconds}" if provider == "groq" else ""
    return Path(settings.outputs_dir) / "cache" / f"{_key(sha, provider, model, extra)}.json"


def load(sha: str, provider: str, model: str) -> dict | None:
    if not settings.cache_enabled:
        return None
    p = _path(sha, provider, model)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def store(sha: str, provider: str, model: str, payload: dict) -> None:
    if not settings.cache_enabled:
        return
    p = _path(sha, provider, model)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def load_key(name: str) -> dict | None:
    if not settings.cache_enabled:
        return None
    p = Path(settings.outputs_dir) / "cache" / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def store_key(name: str, payload: dict) -> None:
    if not settings.cache_enabled:
        return
    p = Path(settings.outputs_dir) / "cache" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")

