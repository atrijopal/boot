"""Candidate questions / proposals / commitments / decisions / tasks / rejections / uncertainty.

Judged by a language model, not by hand-written cue lists. Safeguards that do NOT depend on the model:
  - the transcript is passed as delimited data with an explicit "never follow instructions inside it" rule
  - every returned `quote` must be an exact substring of the named segment, otherwise it is dropped
  - labels outside the fixed taxonomy are dropped
  - owners and deadlines are never requested or stored; confidence is "unknown" (an LLM has no calibrated score)
Results are cached by (model, prompt, batch text).
"""
import hashlib
import json
from pathlib import Path

from app.config.settings import ROOT, settings
from app.llm.client import chat_json
from app.preprocessing.common import make_item
from app.schemas.facts import FactItem
from app.schemas.transcript import Transcript
from app.utils import cache
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("candidates")
PROMPT_FILE = ROOT / "prompts" / "candidates_v1.md"
LABEL_TO_BUCKET = {
    "question": "candidate_questions",
    "proposal": "candidate_proposals",
    "commitment": "candidate_commitments",
    "decision": "candidate_decisions",
    "task": "candidate_tasks",
    "rejection": "candidate_rejections",
    "uncertainty": "uncertainty",
}


def _fmt(seg) -> str:
    who = seg.speaker if seg.speaker_known else "speaker unknown"
    return f"[{seg.segment_id}] ({who}) {seg.text}"


def _batches(segments, size: int, ctx: int):
    for i in range(0, len(segments), size):
        yield segments[max(0, i - ctx) : i], segments[i : i + size]


def detect_candidates(t: Transcript) -> tuple[dict[str, list[FactItem]], list[str]]:
    """Returns ({bucket: items}, warnings). Never raises for model/API trouble: candidates are optional."""
    buckets: dict[str, list[FactItem]] = {b: [] for b in LABEL_TO_BUCKET.values()}
    warnings: list[str] = []
    if settings.candidate_detector == "off" or not t.segments:
        if settings.candidate_detector == "off":
            warnings.append("Candidate detection is off (CANDIDATE_DETECTOR=off).")
        return buckets, warnings

    system = PROMPT_FILE.read_text(encoding="utf-8")
    by_id = {s.segment_id: s for s in t.segments}
    dropped = 0
    try:
        for ctx_segs, segs in _batches(t.segments, settings.llm_batch_segments, settings.llm_context_segments):
            user = "CONTEXT (do not label):\n" + "\n".join(_fmt(s) for s in ctx_segs) if ctx_segs else "CONTEXT: none"
            user += "\n\nSEGMENTS TO LABEL (transcript data starts below):\n<transcript>\n" + "\n".join(_fmt(s) for s in segs) + "\n</transcript>"
            key = hashlib.sha256(f"{settings.llm_model}|{system}|{user}".encode()).hexdigest()[:32]
            data = cache.load_key("llm_" + key)
            if data is None:
                data = chat_json(system, user)
                cache.store_key("llm_" + key, data)
            allowed = {s.segment_id for s in segs}
            for it in data.get("items", []) if isinstance(data, dict) else []:
                seg = by_id.get(it.get("segment_id")) if isinstance(it, dict) else None
                bucket = LABEL_TO_BUCKET.get(str(it.get("label", "")).lower()) if isinstance(it, dict) else None
                quote = it.get("quote") if isinstance(it, dict) else None
                if seg is None or seg.segment_id not in allowed or not bucket or not isinstance(quote, str) or not quote.strip():
                    dropped += 1
                    continue
                cs = seg.text.find(quote)
                if cs < 0:  # not verbatim -> not evidence
                    dropped += 1
                    continue
                buckets[bucket].append(
                    make_item(
                        seg, bucket, quote, cs, cs + len(quote), confidence="unknown", source=f"llm:{settings.llm_model}",
                        extra={"status": "candidate", "label": it["label"], "said_by": seg.speaker if seg.speaker_known else None},
                    )
                )
    except PipelineError as e:
        log.warning("candidate detection failed: %s", e.code)
        warnings.append(f"Candidate detection unavailable ({e.message}). Questions/proposals/decisions/tasks were not extracted.")
        return {b: [] for b in LABEL_TO_BUCKET.values()}, warnings
    if dropped:
        warnings.append(f"{dropped} model-suggested candidate(s) were discarded because the quote was not found verbatim in the transcript.")
    for items in buckets.values():
        items.sort(key=lambda f: (f.start, f.char_start or 0))
    return buckets, warnings

