"""Relate verified decisions / proposals / questions to each other (one call), then apply only code-validated relations."""
import json

from app.config.settings import settings
from app.minutes.chunking import mmss
from app.minutes.grounding import locate
from app.minutes.llm import Ctx, ask
from app.schemas.minutes import Item, Relation
from app.utils.errors import PipelineError

KINDS = {
    "supersedes": ({"decision"}, {"decision"}),
    "contradicts": ({"decision"}, {"decision"}),
    "adopts": ({"decision"}, {"proposal"}),
    "rejects": ({"decision", "proposal"}, {"proposal"}),
    "resolves": ({"decision"}, {"question"}),
}
MAX_ITEMS = 60


def reconcile(ctx: Ctx, items: list[Item]) -> tuple[list[Relation], list[str]]:
    cands = [i for i in items if i.lens in ("decision", "proposal", "question")]
    if not any(i.lens == "decision" for i in cands) or len(cands) < 2:
        return [], []
    warnings: list[str] = []
    if len(cands) > MAX_ITEMS:
        warnings.append(f"Reconciliation looked at the first {MAX_ITEMS} of {len(cands)} decisions/proposals/questions.")
        cands = cands[:MAX_ITEMS]
    by_id = {i.item_id: i for i in cands}
    recs = [{"id": i.item_id, "lens": i.lens, "status": i.status, "time": mmss(i.start), "text": i.text, "quotes": [e.quote for e in i.evidence]} for i in cands]
    user = "ITEMS (data):\n<items>\n" + "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n</items>"
    try:
        data = ask(ctx, "reconcile", "reconcile", user, settings.minutes_verify_model, settings.minutes_verify_effort, "relations", ref="all")
    except PipelineError as e:
        return [], warnings + [f"Reconciliation failed ({e.code}); revised or conflicting decisions are not marked."]
    seg_by_id = {s.segment_id: s for s in ctx.transcript.segments}
    relations: list[Relation] = []
    def log(r, result, reason=""):
        ctx.log.outcome("reconcile", ref="all", source=str(r.get("source")), target=str(r.get("target")), kind=str(r.get("kind")),
                        quote=str(r.get("quote") or "")[:200], result=result, reason=reason)

    for r in data["relations"]:
        if not isinstance(r, dict):
            continue
        src, tgt, kind = by_id.get(r.get("source")), by_id.get(r.get("target")), r.get("kind")
        if not src or not tgt or src is tgt or kind not in KINDS or src.lens not in KINDS[kind][0] or tgt.lens not in KINDS[kind][1]:
            log(r, "ignored", "unknown item, self-relation, or the item types do not fit this relation")
            continue
        if kind == "supersedes" and not src.start > tgt.start:
            log(r, "ignored", "an earlier decision cannot replace a later one")
            continue
        sid, quote = r.get("segment_id"), str(r.get("quote") or "").strip()
        allowed = {e.segment_id for e in src.evidence + tgt.evidence}
        seg = seg_by_id.get(sid)
        if seg is None or sid not in allowed or not quote or locate(seg.text, quote) is None:
            log(r, "ignored", "the quote is not in the evidence of either item")
            continue
        log(r, "applied")
        relations.append(Relation(source=src.item_id, target=tgt.item_id, kind=kind, segment_id=sid, quote=quote))
        if kind == "supersedes":
            tgt.status, tgt.superseded_by = "superseded", src.item_id
        elif kind == "adopts" and tgt.status == "proposed":
            tgt.status = "accepted"
        elif kind == "rejects" and tgt.status == "proposed":
            tgt.status = "rejected"
        elif kind == "resolves":
            tgt.status, tgt.resolved_by = "answered", src.item_id
        elif kind == "contradicts":
            warnings.append(f"Conflicting decisions {src.item_id} and {tgt.item_id}: neither revises the other.")
        for a, b in ((src, tgt), (tgt, src)):
            if b.item_id not in a.related:
                a.related.append(b.item_id)
    return relations, warnings
