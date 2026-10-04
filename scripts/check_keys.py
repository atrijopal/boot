"""Verify the API keys in .env without printing them: python scripts/check_keys.py (or: bash run.sh --check)"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ROOT, settings  # noqa: E402

NEEDED_MODELS = {"whisper-large-v3", settings.refine_model, settings.verify_model}


def check_groq() -> bool:
    if not settings.groq_api_key:
        print("Groq      MISSING  set GROQ_API_KEY in .env  (https://console.groq.com -> API Keys)")
        return False
    try:
        r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {settings.groq_api_key}"}, timeout=20)
    except httpx.HTTPError as e:
        print(f"Groq      UNREACHABLE  ({type(e).__name__}); check your internet connection")
        return False
    if r.status_code in (401, 403):
        print("Groq      INVALID  the key was rejected; create a new one at https://console.groq.com")
        return False
    if r.status_code != 200:
        print(f"Groq      ERROR  HTTP {r.status_code}")
        return False
    have = {m["id"] for m in r.json().get("data", [])}
    missing = sorted(NEEDED_MODELS - have)
    print("Groq      OK       key works" + (f", but these models are not available to you: {', '.join(missing)}" if missing else f", all needed models available ({', '.join(sorted(NEEDED_MODELS))})"))
    return not missing


def check_deepgram() -> bool:
    if not settings.deepgram_api_key:
        print("Deepgram  not set  optional: needed for speaker labels (https://console.deepgram.com -> API Keys)")
        return True
    try:
        r = httpx.get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {settings.deepgram_api_key}"}, timeout=20)
    except httpx.HTTPError as e:
        print(f"Deepgram  UNREACHABLE  ({type(e).__name__})")
        return False
    ok = r.status_code == 200
    print("Deepgram  OK       key works" if ok else f"Deepgram  INVALID  HTTP {r.status_code}; create a new key at https://console.deepgram.com")
    return ok


if __name__ == "__main__":
    if not (ROOT / ".env").is_file():
        print("No .env file found. Run:  cp .env.example .env   then paste your keys.")
        sys.exit(1)
    groq_ok, dg_ok = check_groq(), check_deepgram()
    sys.exit(0 if groq_ok and dg_ok else 1)
