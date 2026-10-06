"""Check configured API credentials without printing them: bash run.sh --check."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings
from app.llm.client import chat_api_key
from app.utils.errors import PipelineError


def check_models(label: str, base_url: str, key: str, needed: set[str]) -> bool:
    try:
        response = httpx.get(base_url.rstrip("/") + "/models",
                             headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except httpx.HTTPError as error:
        print(f"{label}  UNREACHABLE ({type(error).__name__}); check your connection")
        return False
    if response.status_code != 200:
        print(f"{label}  ERROR HTTP {response.status_code}; check the key and endpoint")
        return False
    try:
        available = {row["id"] for row in response.json()["data"]}
    except (ValueError, KeyError, TypeError):
        print(f"{label}  ERROR unreadable model listing")
        return False
    missing = sorted(needed - available)
    if missing:
        print(f"{label}  MISSING MODELS: {', '.join(missing)}")
        return False
    print(f"{label}  OK; configured models available")
    return True


def check_groq() -> bool:
    required = settings.asr_provider == "groq"
    if not settings.groq_api_key:
        print("Groq ASR  MISSING: set GROQ_API_KEY in .env" if required else "Groq ASR  optional, not configured")
        return not required
    needed = {settings.groq_default_model, settings.groq_fallback_model}
    if required and settings.asr_model:
        needed.add(settings.asr_model)
    return check_models("Groq ASR", "https://api.groq.com/openai/v1", settings.groq_api_key, needed)


def check_llm() -> bool:
    try:
        key = chat_api_key()
    except PipelineError as error:
        print(f"Chat LLM  MISSING: {error.message}")
        return False
    needed = {settings.refine_model, settings.phase3_model or settings.llm_model,
              settings.phase3_verify_model or settings.verify_model}
    if settings.candidate_detector == "llm":
        needed.add(settings.llm_model)
    if settings.verify_enabled:
        needed.add(settings.verify_model)
    return check_models("Chat LLM", settings.llm_base_url, key, needed)


def check_deepgram() -> bool:
    if not settings.deepgram_api_key:
        required = settings.asr_provider == "deepgram"
        print("Deepgram  MISSING: set DEEPGRAM_API_KEY in .env" if required else "Deepgram  optional, not configured (no speaker labels)")
        return not required
    try:
        response = httpx.get("https://api.deepgram.com/v1/projects",
                             headers={"Authorization": f"Token {settings.deepgram_api_key}"}, timeout=20)
    except httpx.HTTPError as error:
        print(f"Deepgram  UNREACHABLE ({type(error).__name__})")
        return False
    ok = response.status_code == 200
    print("Deepgram  OK" if ok else f"Deepgram  ERROR HTTP {response.status_code}; check the key and project permissions")
    return ok


def main():
    # Environment variables alone are supported; a physical .env is optional.
    results = [check_groq(), check_llm(), check_deepgram()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
