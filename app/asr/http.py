"""Shared HTTP helper: retry on rate limits / transient errors, map failures to user-safe errors."""
import time

import httpx

from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("asr.http")


def post_with_retry(url: str, *, provider: str, retries: int = 5, **kwargs) -> httpx.Response:
    last = ""
    for attempt in range(retries):
        try:
            resp = httpx.post(url, timeout=httpx.Timeout(600, connect=30), **kwargs)
        except httpx.HTTPError as e:
            last = f"network error ({type(e).__name__})"
            time.sleep(min(2**attempt, 20))
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (401, 403):
            raise PipelineError("API_KEY_INVALID", f"{provider} rejected the API key. Check it in your .env file.")
        if resp.status_code == 413:
            raise PipelineError("API_PAYLOAD_TOO_LARGE", f"{provider} rejected the upload as too large.")
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = float(resp.headers.get("retry-after", 0) or 0) or min(2**attempt * 2, 60)
            last = f"HTTP {resp.status_code}"
            log.warning("%s %s, retrying in %.0fs (attempt %d/%d)", provider, last, wait, attempt + 1, retries)
            time.sleep(min(wait, 120))
            continue
        raise PipelineError("API_ERROR", f"{provider} returned an error (HTTP {resp.status_code}): {resp.text[:200]}")
    code = "API_RATE_LIMITED" if "429" in last else "API_UNAVAILABLE"
    raise PipelineError(code, f"{provider} is unavailable or rate limited ({last}). Please wait a minute and try again.")
