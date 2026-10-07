"""Verify the API keys in .env without printing them: python scripts/check_keys.py (or: bash run.sh --check)"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ROOT, settings  # noqa: E402

from app.llm.client import split_model  # noqa: E402

ALL = {m for m in [settings.fallback_model] if m} | {settings.refine_model, settings.extract_model, settings.review_model, settings.names_model, settings.extract_model_decisions, settings.extract_model_actions,
       settings.extract_model_issues, settings.extract_model_details, settings.extract_model_discussion}
NEEDED = {"whisper-large-v3"} | {split_model(m)[1] for m in ALL if split_model(m)[0] == "groq"}
OTHER = sorted(m for m in ALL if split_model(m)[0] != "groq")


def check_groq() -> bool:
    if not settings.groq_api_key:
        print("Groq      MISSING  set GROQ_API_KEY in .env  (https://console.groq.com -> API Keys)")
        return False
    try:
        r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {settings.groq_api_key}"}, timeout=20)
    except httpx.HTTPError as e:
        print(f"Groq      UNREACHABLE  ({type(e).__name__})")
        return False
    if r.status_code in (401, 403):
        print("Groq      INVALID  the key was rejected; create a new one at https://console.groq.com")
        return False
    if r.status_code != 200:
        print(f"Groq      ERROR  HTTP {r.status_code}")
        return False
    have = {m["id"] for m in r.json().get("data", [])}
    missing = sorted(NEEDED - have)
    for m in OTHER:
        prov = split_model(m)[0]
        print(f"{prov:9} {'OK       key set' if getattr(settings, prov + '_api_key') else 'MISSING  ' + prov.upper() + '_API_KEY is not set'} (used by {m})")
    print("Groq      OK       key works" + (f", but not available to you: {', '.join(missing)}" if missing else f", needed models available ({', '.join(sorted(NEEDED))})"))
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
    print("Deepgram  OK       key works" if r.status_code == 200 else f"Deepgram  INVALID  HTTP {r.status_code}")
    return r.status_code == 200


if __name__ == "__main__":
    if not (ROOT / ".env").is_file():
        print("No .env file found. Run:  cp .env.example .env   then paste your keys.")
        sys.exit(1)
    ok = check_groq() & check_deepgram()
    sys.exit(0 if ok else 1)
