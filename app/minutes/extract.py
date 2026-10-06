"""Extraction nodes: one API call per (call family x chunk), run concurrently, every item passed through the grounding gate."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app.config.settings import settings
from app.minutes.chunking import Chunk
from app.minutes.grounding import ground_item
from app.minutes.lenses import CALLS
from app.minutes.llm import Ctx, ask
from app.schemas.minutes import Item
from app.utils.errors import PipelineError


def extract_call(ctx: Ctx, call: str, chunks: list[Chunk]) -> tuple[list[Item], list[str], dict]:
    """Returns (grounded items, warnings, stats). A failed chunk is reported, never fatal."""
    allowed = CALLS[call]
    prompt = f"extract_{call}"
    seg_by_id = {s.segment_id: s for s in ctx.transcript.segments}
    header = f"PARTICIPANTS: {ctx.participants or 'unknown'}\n\n"

    def work(chunk: Chunk):
        return ask(ctx, f"extract:{call}", prompt, header + chunk.render(), settings.minutes_extract_model, settings.minutes_extract_effort, "items", ref=chunk.chunk_id)

    items: list[Item] = []
    dropped: Counter = Counter()
    warnings: list[str] = []
    raw_count = failed = 0

    def consume(chunk: Chunk, data: dict) -> None:
        nonlocal raw_count
        for raw in data["items"]:
            raw_count += 1
            item, why = ground_item(raw, allowed, chunk, seg_by_id)
            rec = raw if isinstance(raw, dict) else {"raw": str(raw)[:200]}
            ctx.log.outcome("extraction", ref=chunk.chunk_id, call=call, lens=str(rec.get("lens", "")), status=str(rec.get("status", "")),
                            text=str(rec.get("text") or rec.get("value") or "")[:300], result="kept" if item else "dropped", reason=why,
                            evidence=[{"segment_id": e.segment_id, "quote": e.quote} for e in item.evidence] if item else
                            [{"segment_id": str(e.get("segment_id")), "quote": str(e.get("quote"))[:160]} for e in rec.get("evidence", []) if isinstance(e, dict)][:3])
            if item:
                items.append(item)
            else:
                dropped[why] += 1

    retry: list[Chunk] = []
    with ThreadPoolExecutor(max_workers=max(1, settings.minutes_concurrency)) as pool:
        futures = [(c, pool.submit(work, c)) for c in chunks]
        for chunk, fut in futures:
            try:
                consume(chunk, fut.result())
            except PipelineError:
                retry.append(chunk)
    for chunk in retry:  # one more patient attempt, after the burst of parallel calls is over
        try:
            consume(chunk, work(chunk))
        except PipelineError as e:
            failed += 1
            warnings.append(f"{call}: chunk {chunk.chunk_id} failed ({e.code}: {e.message[:200]}); that part of the meeting was not scanned for this lens.")
    if dropped:
        warnings.append(f"{call}: {sum(dropped.values())} of {raw_count} proposed item(s) dropped by the quote/number checks "
                        f"({'; '.join(f'{k}: {v}' for k, v in dropped.most_common(3))}).")
    return items, warnings, {"raw": raw_count, "kept": len(items), "dropped": dict(dropped), "failed_chunks": failed, "chunks": len(chunks)}
