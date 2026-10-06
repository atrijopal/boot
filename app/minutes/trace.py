"""Per-run trace: every LLM call and every graph node, with model, tokens, latency, retries and cache hits.

Written to trace.jsonl next to the minutes. Nothing leaves the machine. (LangSmith is an opt-in extra, see llm.py.)
"""
import json
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class Tracer:
    def __init__(self, path: Path | None = None, status: Callable[[str, str], None] | None = None):
        self.path = path
        self.status = status or (lambda stage, msg: None)
        self.spans: list[dict] = []
        self.lock = threading.Lock()
        self.t0 = time.perf_counter()

    def _add(self, span: dict) -> None:
        span["t"] = round(time.perf_counter() - self.t0, 3)
        with self.lock:
            self.spans.append(span)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(span, ensure_ascii=False) + "\n")

    @contextmanager
    def node(self, name: str, message: str = ""):
        self.status(name, message or name)
        t = time.perf_counter()
        err = None
        try:
            yield
        except Exception as e:  # recorded, then re-raised
            err = f"{type(e).__name__}: {e}"[:200]
            raise
        finally:
            self._add({"kind": "node", "node": name, "secs": round(time.perf_counter() - t, 3), "ok": err is None, "error": err})
            self.status(f"{name}:done", "")

    def llm(self, node: str, prompt: str, model: str, meta: dict | None, cache_hit: bool, in_chars: int, out_chars: int, error: str | None = None):
        usage = (meta or {}).get("usage", {}) or {}
        self._add({
            "kind": "llm", "node": node, "prompt": prompt, "model": model, "cache_hit": cache_hit, "ok": error is None, "error": error,
            "secs": (meta or {}).get("secs", 0.0), "retries": (meta or {}).get("retries", 0), "rate_limited": (meta or {}).get("rate_limited", 0),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "in_chars": in_chars, "out_chars": out_chars,
        })

    def summary(self) -> dict:
        llm = [s for s in self.spans if s["kind"] == "llm"]
        nodes = [s for s in self.spans if s["kind"] == "node"]
        by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        for s in llm:
            m = by_model[s["model"]]
            m["calls"] += 1
            m["prompt_tokens"] += s.get("prompt_tokens") or 0
            m["completion_tokens"] += s.get("completion_tokens") or 0
        return {
            "llm_calls": len(llm),
            "cache_hits": sum(s["cache_hit"] for s in llm),
            "failed_calls": sum(not s["ok"] for s in llm),
            "retries": sum(s.get("retries", 0) for s in llm),
            "rate_limited": sum(s.get("rate_limited", 0) for s in llm),
            "llm_seconds": round(sum(s["secs"] for s in llm), 1),
            "prompt_tokens": sum(s.get("prompt_tokens") or 0 for s in llm),
            "completion_tokens": sum(s.get("completion_tokens") or 0 for s in llm),
            "by_model": dict(by_model),
            "node_seconds": {n["node"]: n["secs"] for n in nodes},
            "wall_seconds": round(time.perf_counter() - self.t0, 1),
        }
