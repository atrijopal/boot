"""Independent semantic check of every grounded item by a larger model, from the transcript context (not just the quotes)."""
import json
from concurrent.futures import ThreadPoolExecutor

from app.config.settings import settings
from app.minutes.chunking import line
from app.minutes.lenses import LENSES, LENS_ORDER, STATUS_MEANING
from app.minutes.llm import Ctx, ask
from app.schemas.minutes import Item, Verification
from app.utils.errors import PipelineError

CONTEXT = 2  # transcript lines on each side of every quote


def _record(ctx: Ctx, it: Item, index: dict[str, int]) -> dict:
    segs = ctx.transcript.segments
    idxs = sorted({j for e in it.evidence for j in range(max(0, index[e.segment_id] - CONTEXT), min(len(segs), index[e.segment_id] + CONTEXT + 1))})
    rec = {"id": it.item_id, "kind_of_item": it.lens, "statement": it.text, "status": it.status,
           "status_meaning": STATUS_MEANING.get((it.lens, it.status), ""),
           "allowed_statuses": {st: STATUS_MEANING[(it.lens, st)] for st in LENSES[it.lens]["statuses"]}, "quotes": [e.quote for e in it.evidence],
           "context": [line(segs[j]) for j in idxs]}
    if it.lens == "action":
        rec["owner"] = it.attrs["owner"]["text"]
        rec["deadline"] = it.attrs.get("deadline")
    if it.lens == "fact":
        rec["label"], rec["value"] = it.attrs["label"], it.attrs["value"]
    return rec


def verify_items(ctx: Ctx, items: list[Item]) -> tuple[list[Item], list[Item], list[str]]:
    """Returns (verified, needs_review, warnings). Verified items get final ids; needs_review items are kept but not used downstream."""
    for n, it in enumerate(items, 1):
        it.item_id = f"tmp_{n:04d}"
    index = {s.segment_id: i for i, s in enumerate(ctx.transcript.segments)}
    batches = [items[i : i + settings.minutes_verify_batch] for i in range(0, len(items), settings.minutes_verify_batch)]
    warnings: list[str] = []
    verdicts: dict[str, dict] = {}
    failed_batches = 0

    def work(batch, ref):
        user = "RECORDS (data):\n<records>\n" + "\n".join(json.dumps(_record(ctx, it, index), ensure_ascii=False) for it in batch) + "\n</records>"
        return ask(ctx, "verify", "verify_items", user, settings.minutes_verify_model, settings.minutes_verify_effort, "verdicts", ref=ref)

    with ThreadPoolExecutor(max_workers=max(1, settings.minutes_concurrency)) as pool:
        futs = [(b, pool.submit(work, b, f"b{n}")) for n, b in enumerate(batches, 1)]
        for batch, fut in futs:
            try:
                data = fut.result()
            except PipelineError as e:
                failed_batches += 1
                warnings.append(f"Verification batch failed ({e.code}: {e.message[:220]}); {len(batch)} item(s) are listed as needs-review.")
                continue
            ids = {it.item_id for it in batch}
            for v in data["verdicts"]:
                if isinstance(v, dict) and v.get("id") in ids and v["id"] not in verdicts:
                    verdicts[v["id"]] = v

    verified, review = [], []
    decisions: list[tuple[Item, dict | None, str, str]] = []  # (item, verdict, original status, outcome)
    for it in items:
        v = verdicts.get(it.item_id)
        original = it.status
        it.verification = Verification(model=settings.minutes_verify_model)
        if v is None:
            it.verification.notes.append("no verdict from the verifier")
            review.append(it)
            decisions.append((it, None, original, "needs_review"))
            continue
        reason = str(v.get("reason") or "")[:240]
        better = v.get("better_status")
        corrected = False
        if v.get("supported") is True and v.get("status_ok") is not True and better in LENSES[it.lens]["statuses"] and better != it.status:
            it.verification.notes.append(f"status corrected from '{it.status}' to '{better}' by the verifier: {reason}")
            it.status = better  # faithful statement, wrong label: keep the item with the label the context supports
            v = {**v, "status_ok": True}
            corrected = True
        if v.get("supported") is True and v.get("status_ok") is True:
            it.verification.status = "verified"
            if it.lens == "action":
                if it.attrs["owner"]["basis"] != "unspecified" and v.get("owner_ok") is False:
                    it.verification.notes.append(f"owner removed: not supported by the context ({it.attrs['owner']['text']})")
                    it.attrs["owner"] = {"text": None, "basis": "unspecified", "speaker": None}
                if it.attrs.get("deadline") and v.get("deadline_ok") is False:
                    it.verification.notes.append(f"deadline removed: not supported by the context ({it.attrs['deadline']})")
                    it.attrs["deadline"] = None
            verified.append(it)
            decisions.append((it, verdicts[it.item_id], original, "verified (status corrected)" if corrected else "verified"))
        else:
            why = "statement not supported" if v.get("supported") is not True else "status label not supported"
            it.verification.notes.append(f"{why}: {reason}" if reason else why)
            review.append(it)
            decisions.append((it, verdicts[it.item_id], original, "needs_review"))

    # final, readable ids: verified items by lens in time order; review items get rev_ ids
    counters: dict[str, int] = {}
    for it in sorted(verified, key=lambda i: (i.start, i.lens)):
        counters[it.lens] = counters.get(it.lens, 0) + 1
        it.item_id = f"{LENSES[it.lens]['prefix']}_{counters[it.lens]:03d}"
    for n, it in enumerate(sorted(review, key=lambda i: (i.start, i.lens)), 1):
        it.item_id = f"rev_{n:03d}"
    for it, v, original, outcome in sorted(decisions, key=lambda d: (d[0].start, d[0].lens)):
        ctx.log.outcome("verification", item_id=it.item_id, lens=it.lens, text=it.text, status_before=original, status_after=it.status, start=it.start,
                        result=outcome, verdict=({k: v.get(k) for k in ("supported", "status_ok", "better_status", "owner_ok", "deadline_ok", "reason")} if v else None),
                        notes=list(it.verification.notes), quote=it.evidence[0].quote)
    order = {l: i for i, l in enumerate(LENS_ORDER)}
    verified.sort(key=lambda i: (order[i.lens], i.start))
    review.sort(key=lambda i: (i.start, i.lens))
    if failed_batches == len(batches) and batches:
        warnings.append("The verifier was unreachable: no item could be confirmed, so the registers are empty. Re-run when the API is available.")
    return verified, review, warnings
