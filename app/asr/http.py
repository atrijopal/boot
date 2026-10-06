"""Shared HTTP helper: retry on rate limits / transient errors, map failures to user-safe errors."""
import time

import httpx

from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("asr.http")


def _api_message(resp: httpx.Response) -> str:
    """The provider's own explanation (for example which limit was hit), if it sent one."""
    try:
        return str(resp.json()["error"]["message"])[:260]
    except Exception:
        return (resp.text or "")[:160]


def _fmt_wait(seconds: float) -> str:
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60)}m" if seconds >= 3600 else f"{int(seconds // 60)}m {int(seconds % 60)}s"


def post_with_retry(url: str, *, provider: str, retries: int = 5, stats: dict | None = None, timeout: httpx.Timeout | None = None, max_wait: float = 120, **kwargs) -> httpx.Response:
    last = ""
    for attempt in range(retries):
        try:
            resp = httpx.post(url, timeout=timeout or httpx.Timeout(600, connect=30), **kwargs)
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
            if resp.status_code == 429 and wait > max_wait:
                # A daily/hourly quota, not a burst limit: waiting a few seconds will not help, so fail now and say why.
                raise PipelineError("API_RATE_LIMITED", f"{provider} says its limit is reached and asks to wait {_fmt_wait(wait)}: {_api_message(resp)}")
            if stats is not None:
                stats["retries"] = stats.get("retries", 0) + 1
                stats["rate_limited"] = stats.get("rate_limited", 0) + (resp.status_code == 429)
            log.warning("%s %s, retrying in %.0fs (attempt %d/%d)", provider, last, wait, attempt + 1, retries)
            time.sleep(min(wait, max_wait))
            continue
        raise PipelineError("API_ERROR", f"{provider} returned an error (HTTP {resp.status_code}): {resp.text[:200]}")
    code = "API_RATE_LIMITED" if "429" in last else "API_UNAVAILABLE"
    raise PipelineError(code, f"{provider} is unavailable or rate limited ({last}). Please wait a minute and try again.")
