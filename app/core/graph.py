"""The pipeline as a LangGraph state machine: transcription -> speakers -> refinement -> meeting report.

Why a graph: every transition is explicit and written to the meeting's session file, so a page (or a restarted server) can see which stage is running, which are
done, what failed and where the user has to act, and a run can be re-entered at the first unfinished stage instead of starting over.

Two stages end in a human checkpoint (`needs_review`): speakers and refinement. In "review" mode the graph stops there and waits; `continue_after` records the
approval and runs on. In "auto" mode it does not stop. The processing code of every stage is the existing phase code; this module only orchestrates it.
"""
import operator
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core import session
from app.core.errors import describe
from app.core.pipeline import load_phase1, load_phase2, run_phase1, run_phase2, run_phase3
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("graph")
ORDER = ["transcription", "speakers", "refinement", "report"]
NODE = {"transcription": "transcribe", "speakers": "speakers", "refinement": "refine", "report": "report"}
STAGE_OF = {v: k for k, v in NODE.items()}


class PipelineState(TypedDict, total=False):
    job_id: str
    path: str | None
    provider: str | None
    mode: str  # "review": stop at the human checkpoints | "auto": do not stop
    until: str  # the last stage to run in this invocation
    outcome: str  # of the last node: ok | review | failed
    trace: Annotated[list[str], operator.add]


def _next_open(job_id: str, start: int = 0) -> str:
    """The first stage from `start` that still has to run, or END. A stage waiting for the user's review halts the run."""
    s = session.load(job_id) or session.derive(job_id)
    for k in ORDER[start:]:
        state = s["stages"][k]["state"]
        if state == "completed":
            continue
        if state == "needs_review":
            return END
        return NODE[k]
    return END


def _entry(state: PipelineState) -> str:
    return _next_open(state["job_id"])


def _after(state: PipelineState) -> str:
    stage = state["trace"][-1].split(":")[0]  # the stage the last node ran
    if state.get("outcome") in ("failed", "review") or stage == state.get("until", "report"):
        return END
    return _next_open(state["job_id"], ORDER.index(stage) + 1)


def _guard(stage_names: tuple[str, ...]):
    """Wrap a node: any failure becomes a recorded, user-readable stage failure instead of an exception."""
    def deco(fn):
        def run(state: PipelineState) -> dict:
            job = state["job_id"]
            t0 = time.time()
            try:
                outcome = fn(state)
            except PipelineError as e:
                outcome = _failed(job, stage_names, e)
            except Exception as e:  # noqa: BLE001 - never leak a stack trace to the user; the cause is logged
                log.exception("job %s: stage %s crashed", job, stage_names[-1])
                outcome = _failed(job, stage_names, e)
            return {"outcome": outcome, "trace": [f"{STAGE_OF[fn.__name__]}:{outcome}:{round(time.time() - t0, 1)}s"]}
        run.__name__ = fn.__name__
        return run
    return deco


def _failed(job: str, stage_names: tuple[str, ...], err: Exception) -> str:
    s = session.load(job) or session.derive(job)
    # the failing stage is the one that was running; for audio + transcription that is whichever has not finished
    current = next((k for k in stage_names if s["stages"][k]["state"] == "processing"), stage_names[-1])
    session.fail(job, current, describe(err))
    return "failed"


@_guard(("audio", "transcription"))
def transcribe(state: PipelineState) -> str:
    job = state["job_id"]
    session.begin(job, "audio", "Checking and preparing the audio")

    def say(stage: str, message: str = "") -> None:
        s = session.load(job)
        if stage == "transcribe":  # product wording only: the service behind the step is not the user's concern
            message = "Using the saved transcript" if "Reusing" in message else "Converting speech to text"
        elif stage == "extract_facts":
            message = "Finding numbers, dates, names and terms"
        if stage in ("transcribe", "extract_facts") and s and s["stages"]["audio"]["state"] == "processing":
            session.finish(job, "audio", "completed", "Audio is readable")
            session.begin(job, "transcription", message)
        else:
            session.progress(job, "transcription" if s and s["stages"]["transcription"]["state"] == "processing" else "audio", message)

    p1 = run_phase1(state["path"], job_id=job, provider_name=state.get("provider"), status=say)
    s = session.load(job)
    if s["stages"]["audio"]["state"] == "processing":
        session.finish(job, "audio", "completed", "Audio is readable")
    if s["stages"]["transcription"]["state"] != "processing":
        session.begin(job, "transcription", "")

    def f(x):
        x["audio"] = {"duration_seconds": p1.audio.duration_seconds, "format": p1.audio.format, "file_size_bytes": p1.audio.file_size_bytes, "channels": p1.audio.original_channels}
    session.update(job, f)
    session.finish(job, "transcription", "completed", f"{len(p1.transcript.segments)} lines transcribed")
    return "ok"


@_guard(("speakers",))
def speakers(state: PipelineState) -> str:
    job = state["job_id"]
    session.begin(job, "speakers", "Finding the speakers")
    p2 = run_phase2(job, status=lambda st, m="": session.progress(job, "speakers", m), only="names")
    p1 = load_phase1(job)
    labels = {s.speaker for s in p2.refined.segments if s.speaker_known}
    if not labels:
        session.finish(job, "speakers", "completed", "No speaker labels in this recording")
        return "ok"
    pending = sum(1 for x in p2.name_suggestions if x.name and x.status == "suggested")
    msg = f"{len(labels)} speaker{'s' if len(labels) != 1 else ''} found" + (f", {len(p2.name_suggestions)} name suggestion{'s' if len(p2.name_suggestions) != 1 else ''}" if p2.name_suggestions else ", no names suggested")
    if state.get("mode") == "review":
        session.finish(job, "speakers", "needs_review", msg)
        return "review"
    session.finish(job, "speakers", "completed", msg)
    return "ok"


@_guard(("refinement",))
def refine(state: PipelineState) -> str:
    job = state["job_id"]
    session.begin(job, "refinement", "Checking the transcript for recognition errors")
    p2 = run_phase2(job, status=lambda st, m="": session.progress(job, "refinement", m), only="refine")
    n = len(p2.edits)
    msg = f"{n} correction{'s' if n != 1 else ''} proposed" if n else "No corrections were needed"
    if n and state.get("mode") == "review":
        session.finish(job, "refinement", "needs_review", msg)
        return "review"
    session.finish(job, "refinement", "completed", msg)
    return "ok"


@_guard(("report",))
def report(state: PipelineState) -> str:
    job = state["job_id"]
    session.begin(job, "report", "Producing the meeting record")
    r = run_phase3(job, status=lambda st, m="": session.progress(job, "report", m))
    session.finish(job, "report", "completed", f"{len(r.records)} items recorded" + ("" if r.review_ran else "; the final check did not run"))
    return "ok"


def build():
    g = StateGraph(PipelineState)
    for name, fn in (("transcribe", transcribe), ("speakers", speakers), ("refine", refine), ("report", report)):
        g.add_node(name, fn)
    targets = {n: n for n in NODE.values()} | {END: END}
    g.add_conditional_edges(START, _entry, targets)
    for n in NODE.values():
        g.add_conditional_edges(n, _after, targets)
    return g.compile()


GRAPH = build()


def run(job_id: str, path: str | None = None, provider: str | None = None, mode: str = "review", until: str = "report") -> PipelineState:
    """Run the graph from the first unfinished stage until `until`, a human checkpoint, or a failure. Safe to call again to resume."""
    return GRAPH.invoke({"job_id": job_id, "path": path, "provider": provider, "mode": mode, "until": until, "trace": []}, {"recursion_limit": 25})


def approve(job_id: str, stage: str) -> dict:
    """The user has reviewed `stage` (speakers or refinement): mark it done so the graph can move on."""
    s = session.load(job_id) or session.derive(job_id)
    if s["stages"][stage]["state"] != "needs_review":
        raise PipelineError("BAD_STAGE", f"The {stage} step is not waiting for review.")
    return session.finish(job_id, stage, "completed", s["stages"][stage]["message"])


def retry(job_id: str, stage: str) -> None:
    """Run `stage` again (and the stages after it). Finished work before it is kept."""
    if stage not in ORDER:
        raise PipelineError("BAD_STAGE", f"Unknown stage '{stage}'.")
    session.reset_from(job_id, stage)
