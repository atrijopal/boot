"""The final-report stage: write topic bullets and the executive summary from VERIFIED items only, then check every claim."""
import json
from concurrent.futures import ThreadPoolExecutor

from app.config.settings import settings
from app.minutes.chunking import line
from app.minutes.grounding import numbers_in
from app.minutes.llm import Ctx, ask
from app.schemas.minutes import Claim, Item, Topic
from app.utils.errors import PipelineError

MAX_CHARS = 14000
CLAIM_BATCH = 12


def _item_line(it: Item) -> dict:
    d = {"id": it.item_id, "lens": it.lens, "status": it.status, "text": it.text}
    if it.lens == "action":
        if it.attrs["owner"]["text"]:
            d["owner"] = it.attrs["owner"]["text"]
        if it.attrs.get("deadline"):
            d["deadline"] = it.attrs["deadline"]
    return d


def _check_claims(ctx: Ctx, node: str, claims: list[tuple[str, Claim, str]]) -> dict[str, tuple[bool, str]]:
    """claims = [(claim_id, claim, source_text)]. Returns {claim_id: (supported, reason)}. Missing verdicts count as unsupported."""
    out: dict[str, tuple[bool, str]] = {}
    batches = [claims[i : i + CLAIM_BATCH] for i in range(0, len(claims), CLAIM_BATCH)]

    def work(batch, ref):
        user = "CLAIMS (data):\n<claims>\n" + "\n".join(json.dumps({"id": cid, "claim": c.text, "sources": src}, ensure_ascii=False) for cid, c, src in batch) + "\n</claims>"
        return ask(ctx, node, "verify_claims", user, settings.minutes_verify_model, settings.minutes_verify_effort, "verdicts", ref=ref)

    with ThreadPoolExecutor(max_workers=max(1, settings.minutes_concurrency)) as pool:
        for batch, fut in [(b, pool.submit(work, b, f"b{k}")) for k, b in enumerate(batches, 1)]:
            try:
                data = fut.result()
            except PipelineError:
                continue
            ids = {cid for cid, _, _ in batch}
            for v in data["verdicts"]:
                if isinstance(v, dict) and v.get("id") in ids:
                    out[v["id"]] = (v.get("supported") is True, str(v.get("reason") or "")[:160])
    return out


def _extractive(items: list[Item]) -> list[Claim]:
    """Deterministic fallback: restate verified items (never new wording)."""
    claims = []
    for it in items:
        text = f"{it.text}"
        if it.lens == "action":
            who = it.attrs["owner"]["text"] or "unspecified"
            text += f" (owner: {who}; deadline: {it.attrs.get('deadline') or 'unspecified'})"
        claims.append(Claim(text=text, item_ids=[it.item_id], segment_ids=[e.segment_id for e in it.evidence], verified=True))
    return claims


def summarize_topics(ctx: Ctx, topics: list[Topic], items: list[Item]) -> tuple[list[Topic], list[str], bool]:
    """Fills topic.claims. Returns (topics, warnings, used_fallback)."""
    segs = ctx.transcript.segments
    index = {s.segment_id: i for i, s in enumerate(segs)}
    warnings: list[str] = []
    fallback = False

    def topic_parts(t: Topic):
        a, b = index[t.start_segment], index[t.end_segment]
        chunk, size, parts = [], 0, []
        for s in segs[a : b + 1]:
            if chunk and size + len(s.text) > MAX_CHARS:
                parts.append(chunk)
                chunk, size = [], 0
            chunk.append(s)
            size += len(s.text) + 40
        parts.append(chunk)
        return parts

    def in_topic(t: Topic, it: Item) -> bool:
        return index[t.start_segment] <= index[it.evidence[0].segment_id] <= index[t.end_segment]

    def work(t: Topic, part):
        mine = [i for i in items if in_topic(t, i) and any(index[e.segment_id] >= index[part[0].segment_id] and index[e.segment_id] <= index[part[-1].segment_id] for e in i.evidence)]
        user = (f"TOPIC: {t.title}\n\nSEGMENTS (data):\n<transcript>\n" + "\n".join(line(s) for s in part) + "\n</transcript>\n\nVERIFIED ITEMS (data):\n<items>\n"
                + "\n".join(json.dumps(_item_line(i), ensure_ascii=False) for i in mine) + "\n</items>")
        return mine, ask(ctx, f"summarize:{t.topic_id}", "topic_summary", user, settings.minutes_write_model, settings.minutes_write_effort, "claims", ref=t.topic_id)

    candidates: dict[str, list[Claim]] = {t.topic_id: [] for t in topics}
    jobs = [(t, p) for t in topics for p in topic_parts(t)]
    with ThreadPoolExecutor(max_workers=max(1, settings.minutes_concurrency)) as pool:
        futs = [(t, p, pool.submit(work, t, p)) for t, p in jobs]
        for t, part, fut in futs:
            try:
                mine, data = fut.result()
            except PipelineError as e:
                warnings.append(f"{t.topic_id}: narrative generation failed ({e.code}).")
                continue
            seg_ids = {s.segment_id for s in part}
            by_item = {i.item_id: i for i in mine}
            for raw in data["claims"]:
                if not isinstance(raw, dict):
                    continue
                text = str(raw.get("text") or "").strip()
                sids = [x for x in raw.get("segment_ids", []) if x in seg_ids] if isinstance(raw.get("segment_ids"), list) else []
                iids = [x for x in raw.get("item_ids", []) if x in by_item] if isinstance(raw.get("item_ids"), list) else []
                if not 3 <= len(text) <= 700 or not (sids or iids):
                    ctx.log.outcome("topic_claims", ref=t.topic_id, text=text[:300], segment_ids=sids, item_ids=iids, result="dropped", reason="empty text or no valid source cited")
                    continue
                cited = " ".join([segs[index[s]].text for s in sids] + [by_item[i].text for i in iids])
                if not numbers_in(text) <= numbers_in(cited):
                    ctx.log.outcome("topic_claims", ref=t.topic_id, text=text[:300], segment_ids=sids, item_ids=iids, result="dropped", reason="introduces a number that is not in the cited sources")
                    continue
                candidates[t.topic_id].append(Claim(text=text, item_ids=iids, segment_ids=sids))

    # independent check of every claim against its cited sources
    by_item_all = {i.item_id: i for i in items}
    flat = [(f"{tid}:{n}", c, {"segments": [line(segs[index[s]]) for s in c.segment_ids], "items": [_item_line(by_item_all[i]) for i in c.item_ids if i in by_item_all]})
            for tid, cs in candidates.items() for n, c in enumerate(cs)]
    verdict = _check_claims(ctx, "verify_topic_claims", flat) if flat else {}
    dropped = 0
    for t in topics:
        kept = []
        for n, c in enumerate(candidates[t.topic_id]):
            ok, why = verdict.get(f"{t.topic_id}:{n}", (False, "no verdict from the checker"))
            ctx.log.outcome("topic_claims", ref=t.topic_id, text=c.text[:300], segment_ids=c.segment_ids, item_ids=c.item_ids,
                            result="verified" if ok else "dropped", reason=why)
            if ok:
                c.verified = True
                kept.append(c)
            else:
                dropped += 1
                if dropped <= 4:
                    warnings.append(f"Dropped bullet in {t.topic_id} ({c.text[:70]}...): {why or 'not supported'}")
        t.claims = kept
        t.item_ids = [i.item_id for i in items if in_topic(t, i)]
        if not kept:
            mine = [i for i in items if in_topic(t, i) and i.lens in ("decision", "action", "question", "fact", "risk")]
            if mine:
                t.claims, t.note, fallback = _extractive(mine), "Narrative unavailable; showing the verified items of this topic.", True
                ctx.log.outcome("topic_claims", ref=t.topic_id, text="(fallback) restated the verified items of this topic", segment_ids=[], item_ids=[i.item_id for i in mine], result="fallback", reason="no generated bullet passed checking")
            else:
                t.note = "No verified narrative or items for this topic."
    if dropped:
        warnings.append(f"{dropped} topic bullet(s) were dropped because the checker did not confirm them.")
    return topics, warnings, fallback


def executive_summary(ctx: Ctx, topics: list[Topic], items: list[Item]) -> tuple[list[Claim], list[str], bool]:
    warnings: list[str] = []
    pick = {"decision": 12, "action": 12, "question": 6, "fact": 8, "risk": 5}
    chosen: list[Item] = []
    for lens, k in pick.items():
        chosen += [i for i in items if i.lens == lens][:k]
    by_item = {i.item_id: i for i in chosen}
    by_topic = {t.topic_id: t for t in topics}
    user = ("TOPICS (data):\n<topics>\n" + "\n".join(json.dumps({"id": t.topic_id, "title": t.title, "bullets": [c.text for c in t.claims]}, ensure_ascii=False) for t in topics)
            + "\n</topics>\n\nVERIFIED ITEMS (data):\n<items>\n" + "\n".join(json.dumps(_item_line(i), ensure_ascii=False) for i in chosen) + "\n</items>")
    claims: list[Claim] = []
    try:
        data = ask(ctx, "executive", "executive", user, settings.minutes_write_model, settings.minutes_write_effort, "claims", ref="x")
    except PipelineError as e:
        data = {"claims": []}
        warnings.append(f"Executive summary generation failed ({e.code}).")
    cands: list[tuple[Claim, list[str]]] = []
    for raw in data["claims"]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        iids = [x for x in raw.get("item_ids", []) if x in by_item] if isinstance(raw.get("item_ids"), list) else []
        tids = [x for x in raw.get("topic_ids", []) if x in by_topic] if isinstance(raw.get("topic_ids"), list) else []
        if not 3 <= len(text) <= 600 or not (iids or tids):
            ctx.log.outcome("executive_claims", ref="x", text=text[:300], item_ids=iids, topic_ids=tids, result="dropped", reason="empty text or no valid source cited")
            continue
        cited = " ".join([by_item[i].text for i in iids] + [c.text for t in tids for c in by_topic[t].claims])
        if numbers_in(text) <= numbers_in(cited):
            cands.append((Claim(text=text, item_ids=iids), tids))
        else:
            ctx.log.outcome("executive_claims", ref="x", text=text[:300], item_ids=iids, topic_ids=tids, result="dropped", reason="introduces a number that is not in the cited sources")
    flat = [(f"x:{n}", c, {"items": [_item_line(by_item[i]) for i in c.item_ids], "topic_bullets": [b.text for t in tids for b in by_topic[t].claims]}) for n, (c, tids) in enumerate(cands)]
    verdict = _check_claims(ctx, "verify_executive", flat) if flat else {}
    for n, (c, tids) in enumerate(cands):
        ok, why = verdict.get(f"x:{n}", (False, "no verdict from the checker"))
        ctx.log.outcome("executive_claims", ref="x", text=c.text[:300], item_ids=c.item_ids, topic_ids=tids, result="verified" if ok else "dropped", reason=why)
        if ok:
            c.verified = True
            claims.append(c)
        elif n < 4:
            warnings.append(f"Dropped executive sentence ({c.text[:70]}...): {why or 'not supported'}")
    if len(claims) < len(cands):
        warnings.append(f"{len(cands) - len(claims)} executive-summary sentence(s) were dropped because the checker did not confirm them.")
    fallback = False
    if not claims:
        key = [i for i in chosen if i.lens in ("decision", "action")][:6] or chosen[:6]
        claims, fallback = _extractive(key), bool(key)
        if key:
            warnings.append("Executive summary generation did not pass checking; showing the verified decisions and actions instead.")
    return claims, warnings, fallback
