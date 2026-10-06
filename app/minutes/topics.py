"""Topic segmentation: an LLM proposes contiguous topics; code makes the ranges valid, complete and bounded."""
import json
from concurrent.futures import ThreadPoolExecutor

from app.config.settings import settings
from app.minutes.chunking import mmss, who
from app.minutes.llm import Ctx, ask
from app.minutes.merge import similarity
from app.schemas.minutes import Topic
from app.utils.errors import PipelineError

WINDOW = 120  # segments per segmentation call
MIN_SEGMENTS = 3


def _condensed(seg) -> str:
    return f"[{seg.segment_id}] {mmss(seg.start)} {who(seg).split(' / ')[0]}: {seg.text[:140]}"


def normalize_ranges(spans: list[tuple[str, int, int]], n: int, max_topics: int) -> list[tuple[str, int, int]]:
    """spans = (title, start_idx, end_idx). Returns contiguous, non-overlapping ranges covering 0..n-1, merged to a sane count."""
    spans = sorted((s for s in spans if 0 <= s[1] <= s[2] < n), key=lambda s: (s[1], s[2]))
    out: list[list] = []
    for title, a, b in spans:
        if out and a <= out[-1][2]:
            a = out[-1][2] + 1
            if a > b:
                continue
        out.append([title, a, b])
    if not out:
        return [("Full discussion", 0, n - 1)]
    out[0][1] = 0
    for k in range(1, len(out)):
        out[k - 1][2] = out[k][1] - 1
    out[-1][2] = n - 1
    # fold tiny topics into their predecessor, then cap the count by merging the shortest neighbours
    k = 1
    while k < len(out):
        if out[k][2] - out[k][1] + 1 < MIN_SEGMENTS:
            out[k - 1][2] = out[k][2]
            del out[k]
        else:
            k += 1
    if len(out) > 1 and out[0][2] - out[0][1] + 1 < MIN_SEGMENTS:
        out[1][1] = 0
        del out[0]
    while len(out) > max_topics:
        k = min(range(len(out) - 1), key=lambda i: (out[i][2] - out[i][1]) + (out[i + 1][2] - out[i + 1][1]))
        out[k][2] = out[k + 1][2]
        del out[k + 1]
    return [(t, a, b) for t, a, b in out]


def segment_topics(ctx: Ctx) -> tuple[list[Topic], list[str]]:
    segs = ctx.transcript.segments
    index = {s.segment_id: i for i, s in enumerate(segs)}
    windows = [segs[i : i + WINDOW] for i in range(0, len(segs), WINDOW)]
    warnings: list[str] = []

    def work(win, ref):
        user = "SEGMENTS (data):\n<transcript>\n" + "\n".join(_condensed(s) for s in win) + "\n</transcript>"
        return ask(ctx, "topics", "topics", user, settings.minutes_topics_model, settings.minutes_topics_effort, "topics", ref=ref)

    spans: list[tuple[str, int, int]] = []
    with ThreadPoolExecutor(max_workers=max(1, settings.minutes_concurrency)) as pool:
        for n, (win, fut) in enumerate([(w, pool.submit(work, w, f"w{k}")) for k, w in enumerate(windows, 1)], 1):
            try:
                data = fut.result()
            except PipelineError as e:
                warnings.append(f"Topic segmentation failed for part of the meeting ({e.code}: {e.message[:200]}).")
                continue
            for t in data["topics"]:
                ok = isinstance(t, dict) and t.get("start_segment") in index and t.get("end_segment") in index and str(t.get("title") or "").strip()
                ctx.log.outcome("topics", ref=f"w{n}", title=str(t.get("title") if isinstance(t, dict) else t)[:80], start_segment=str(t.get("start_segment") if isinstance(t, dict) else ""),
                                end_segment=str(t.get("end_segment") if isinstance(t, dict) else ""), result="kept" if ok else "dropped",
                                reason="" if ok else "unknown segment ids or empty title")
                if ok:
                    spans.append((" ".join(str(t["title"]).split())[:80], index[t["start_segment"]], index[t["end_segment"]]))
    if not spans:
        warnings.append("No topic structure could be determined; the meeting is shown as one section.")
    merged: list[tuple[str, int, int]] = []
    for sp in sorted(spans, key=lambda s: s[1]):  # join a topic that continues across a window boundary
        if merged and sp[1] == merged[-1][2] + 1 and similarity(sp[0], merged[-1][0]) >= 0.7:
            merged[-1] = (merged[-1][0], merged[-1][1], sp[2])
        else:
            merged.append(sp)
    final = normalize_ranges(merged, len(segs), settings.minutes_max_topics)
    topics = [Topic(topic_id=f"t{k:02d}", title=title, start_segment=segs[a].segment_id, end_segment=segs[b].segment_id,
                    start=segs[a].start, end=segs[b].end) for k, (title, a, b) in enumerate(final, 1)]
    for t in topics:
        ctx.log.outcome("topics_final", topic_id=t.topic_id, title=t.title, start_segment=t.start_segment, end_segment=t.end_segment, start=t.start, end=t.end)
    return topics, warnings
