"""Ask the refinement LLM for small, exact edits. It never returns rewritten text."""
import hashlib

from app.config.settings import ROOT, settings
from app.llm.client import chat_json
from app.refinement.context import hints_for, segment_line, term_block
from app.schemas.facts import ProtectedFacts
from app.schemas.transcript import Transcript
from app.utils import cache
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("refine")
PROMPT_FILE = ROOT / "prompts" / "refine_v1.md"


def propose_edits(
    t: Transcript, facts: ProtectedFacts, diffs_by_seg: dict[str, list[dict]]
) -> tuple[list[dict], list[str]]:
    """Returns (raw edit dicts, warnings). Raises PipelineError only if every batch failed."""
    system = PROMPT_FILE.read_text(encoding="utf-8")
    header = term_block(facts)
    segs = t.segments
    size, ctx = settings.refine_batch_segments, settings.refine_context_segments
    raw: list[dict] = []
    warnings: list[str] = []
    failures = batches = 0
    for i in range(0, len(segs), size):
        batches += 1
        context = segs[max(0, i - ctx) : i]
        batch = segs[i : i + size]
        lines = []
        for s in batch:
            lines.append(segment_line(s))
            h = hints_for(s, facts, diffs_by_seg.get(s.segment_id, []))
            if h:
                lines.append(h)
        user = (
            header
            + "\n\nCONTEXT (do not edit):\n"
            + ("\n".join(segment_line(s) for s in context) or "none")
            + "\n\nSEGMENTS TO CHECK (transcript data starts below):\n<transcript>\n"
            + "\n".join(lines)
            + "\n</transcript>"
        )
        key = "refine_" + hashlib.sha256(f"{settings.refine_model}|{settings.refine_reasoning_effort}|{system}|{user}".encode()).hexdigest()[:32]
        data = cache.load_key(key)
        try:
            if data is None:
                data = chat_json(system, user, model=settings.refine_model, reasoning_effort=settings.refine_reasoning_effort)
                cache.store_key(key, data)
        except PipelineError as e:
            failures += 1
            warnings.append(f"Refinement batch {batches} failed ({e.message}).")
            continue
        allowed = {s.segment_id for s in batch}
        items = data.get("edits", []) if isinstance(data, dict) else []
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("segment_id") in allowed:
                raw.append(it)
            else:
                warnings.append("An edit for a segment outside the current batch was ignored.")
    if batches and failures == batches:
        raise PipelineError("REFINE_UNAVAILABLE", "The refinement model could not be reached. " + (warnings[0] if warnings else ""))
    return raw, warnings

