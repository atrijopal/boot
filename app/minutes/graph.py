"""The Phase 3 workflow as a LangGraph state graph.

            +--> extract_all (one call per chunk, all lenses) ---+     [mode "separate": four calls per chunk:
 chunk  --->+                                                   +--> verify --> reconcile --+   agreements / actions /
            |                                                   |                           |   questions_risks / facts]
 topics (in parallel with all of the above) ----------------------------------------------+--> summarize --> executive
"""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config.settings import settings
from app.minutes.chunking import Chunk, make_chunks
from app.minutes.extract import extract_call
from app.minutes.figures import flag_number_mismatches
from app.minutes.llm import Ctx
from app.minutes.merge import dedupe
from app.minutes.reconcile import reconcile
from app.minutes.summaries import executive_summary, summarize_topics
from app.minutes.topics import segment_topics
from app.minutes.verify import verify_items
from app.schemas.minutes import Claim, Item, Relation, Topic

SEPARATE = ("agreements", "actions", "questions_risks", "facts")


def extractors() -> tuple[str, ...]:
    return ("all",) if settings.minutes_extract_mode == "combined" else SEPARATE


def _merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    chunks: list[Chunk]
    raw_items: Annotated[list[Item], operator.add]
    warnings: Annotated[list[str], operator.add]
    stats: Annotated[dict, _merge_dicts]
    verified: list[Item]
    review: list[Item]
    relations: list[Relation]
    topics: list[Topic]
    executive: list[Claim]


def build_graph(ctx: Ctx):
    tr = ctx.tracer

    def chunk(state: State):
        with tr.node("chunk", "Splitting the refined transcript into windows"):
            chunks = make_chunks(ctx.transcript, settings.minutes_chunk_segments, settings.minutes_context_segments)
        return {"chunks": chunks, "stats": {"chunks": len(chunks)}}

    def make_extractor(call: str):
        def node(state: State):
            with tr.node(f"extract_{call}", f"Extracting {call.replace('_', ' / ')}"):
                items, warns, stats = extract_call(ctx, call, state["chunks"])
            return {"raw_items": items, "warnings": warns, "stats": {f"extract_{call}": stats}}
        return node

    def topics(state: State):
        with tr.node("topics", "Finding the topics of the meeting"):
            t, warns = segment_topics(ctx)
        return {"topics": t, "warnings": warns}

    def verify(state: State):
        with tr.node("verify", "Checking every extracted item against the transcript"):
            raw = state.get("raw_items", [])
            merged = dedupe(raw)
            ctx.log.outcome("merge", before=len(raw), after=len(merged), note="duplicates from overlapping windows or repeated mentions were merged; evidence quotes were combined")
            verified, review, warns = verify_items(ctx, merged) if merged else ([], [], [])
        return {"verified": verified, "review": review, "warnings": warns,
                "stats": {"verify": {"merged": len(merged), "verified": len(verified), "needs_review": len(review)}}}

    def reconcile_node(state: State):
        with tr.node("reconcile", "Linking decisions, proposals and questions"):
            items = state.get("verified", [])
            relations, warns = reconcile(ctx, items)
            warns += flag_number_mismatches(items, ctx.facts) if ctx.facts else []
        return {"verified": items, "relations": relations, "warnings": warns}

    def summarize(state: State):
        with tr.node("summarize", "Writing and checking the topic minutes"):
            t, warns, fb = summarize_topics(ctx, state["topics"], state.get("verified", []))
        return {"topics": t, "warnings": warns, "stats": {"summaries_fallback": int(fb)}}

    def executive(state: State):
        with tr.node("executive", "Writing and checking the executive summary"):
            claims, warns, fb = executive_summary(ctx, state["topics"], state.get("verified", []))
        return {"executive": claims, "warnings": warns, "stats": {"executive_fallback": int(fb)}}

    EXTRACTORS = extractors()
    g = StateGraph(State)
    g.add_node("chunk", chunk)
    for call in EXTRACTORS:
        g.add_node(f"extract_{call}", make_extractor(call))
    g.add_node("topics", topics)
    g.add_node("verify", verify)
    g.add_node("reconcile", reconcile_node)
    g.add_node("summarize", summarize)
    g.add_node("executive", executive)

    g.add_edge(START, "chunk")
    g.add_edge(START, "topics")  # independent of extraction: runs alongside it
    for call in EXTRACTORS:
        g.add_edge("chunk", f"extract_{call}")
    g.add_edge([f"extract_{c}" for c in EXTRACTORS], "verify")
    g.add_edge("verify", "reconcile")
    g.add_edge(["reconcile", "topics"], "summarize")
    g.add_edge("summarize", "executive")
    g.add_edge("executive", END)
    return g.compile()


def mermaid(ctx: Ctx) -> str:
    return build_graph(ctx).get_graph().draw_mermaid()
