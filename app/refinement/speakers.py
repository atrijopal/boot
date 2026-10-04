"""Speaker handling for Phase 2.

1. WHO speaks (which voice) is acoustic: labels come from diarization (Deepgram) and are copied onto the primary
   transcript's segments by timestamp overlap. No LLM decides this.
2. WHAT each speaker is called is read from the transcript by an LLM, but only with verbatim evidence that code
   verifies. A speaker without evidence stays SPEAKER_xx. Names are never invented.
"""
import bisect
import hashlib
import re
from collections import Counter, defaultdict

from app.config.settings import ROOT, settings
from app.llm.client import chat_json
from app.refinement.validate import similarity
from app.schemas.refinement import SegmentSpeaker, SpeakerName
from app.schemas.transcript import Transcript
from app.utils import cache
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("speakers")
PROMPT_FILE = ROOT / "prompts" / "speakers_v1.md"
BASES = {"self_introduction", "addressed_by_others", "introduced_by_others"}
MIN_COVERAGE_SECONDS = 0.2
MIXED_BELOW = 0.8
NAME_RE = re.compile(r"^[A-Z][A-Za-z'’\-\.]*(?: [A-Z][A-Za-z'’\-\.]*){0,3}$")


# ------------------------------------------------------------------ 1) voices
def transplant(primary: Transcript, secondary: Transcript) -> dict[str, SegmentSpeaker]:
    """Assign each primary segment the speaker who talks most during it, using the diarized words of `secondary`."""
    words = sorted((w.start, w.end, w.speaker) for s in secondary.segments for w in s.words if w.speaker and w.speaker != "SPEAKER_UNKNOWN")
    if not words:
        return {}
    starts = [w[0] for w in words]
    max_len = max(w[1] - w[0] for w in words)
    out: dict[str, SegmentSpeaker] = {}
    for seg in primary.segments:
        dur: Counter = Counter()
        lo = bisect.bisect_left(starts, seg.start - max_len - 0.01)
        for a, b, spk in words[lo:]:
            if a >= seg.end:
                break
            dur[spk] += max(0.0, min(b, seg.end) - max(a, seg.start))
        total = sum(dur.values())
        if total < MIN_COVERAGE_SECONDS:
            out[seg.segment_id] = SegmentSpeaker(speaker="SPEAKER_UNKNOWN", speaker_known=False, share=0.0)
            continue
        ranked = dur.most_common(2)
        top, share = ranked[0][0], ranked[0][1] / total
        run, run_share = (ranked[1][0], ranked[1][1] / total) if len(ranked) > 1 else (None, 0.0)
        out[seg.segment_id] = SegmentSpeaker(
            speaker=top, speaker_known=True, share=round(share, 3), mixed=share < MIXED_BELOW and run_share >= 0.2,
            runner_up=run, runner_up_share=round(run_share, 3),
        )
    return out


def from_provider(t: Transcript) -> dict[str, SegmentSpeaker]:
    return {s.segment_id: SegmentSpeaker(speaker=s.speaker, speaker_known=s.speaker_known, share=1.0 if s.speaker_known else 0.0) for s in t.segments}


def apply_speakers(t: Transcript, assignments: dict[str, SegmentSpeaker]) -> Transcript:
    if not assignments:
        return t
    segs = []
    for s in t.segments:
        a = assignments.get(s.segment_id)
        segs.append(s.model_copy(update={"speaker": a.speaker, "speaker_known": a.speaker_known}) if a else s)
    return t.model_copy(update={"segments": segs, "speakers_known": any(x.speaker_known for x in assignments.values())})


# ------------------------------------------------------------------ 2) names
def _valid_name(name: str) -> bool:
    return isinstance(name, str) and len(name) <= 40 and NAME_RE.match(name.strip()) is not None


def _word_in(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text, re.I) is not None


def propose_names(t: Transcript) -> tuple[list[dict], list[str]]:
    """LLM proposals over the (refined) transcript. Returns (raw proposals, warnings). Never raises for API trouble."""
    system = PROMPT_FILE.read_text()
    lines = [f"[{s.segment_id}] {s.speaker}: {s.text}" for s in t.segments]
    size = 80
    raw: list[dict] = []
    warnings: list[str] = []
    for i in range(0, len(lines), size):
        user = "TRANSCRIPT (data starts below):\n<transcript>\n" + "\n".join(lines[i : i + size]) + "\n</transcript>"
        key = "spk_" + hashlib.sha256(f"{settings.refine_model}|{system}|{user}".encode()).hexdigest()[:32]
        data = cache.load_key(key)
        try:
            if data is None:
                data = chat_json(system, user, model=settings.refine_model, reasoning_effort=settings.refine_reasoning_effort)
                cache.store_key(key, data)
        except PipelineError as e:
            warnings.append(f"Speaker naming unavailable ({e.message}).")
            return [], warnings
        items = data.get("speakers", []) if isinstance(data, dict) else []
        raw += [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    return raw, warnings


def gate_names(t: Transcript, raw: list[dict]) -> list[SpeakerName]:
    """Verify every proposal in code. self_introduction can auto-apply; weaker bases are only ever suggested."""
    by_id = {s.segment_id: (i, s) for i, s in enumerate(t.segments)}
    labels = {s.speaker for s in t.segments if s.speaker_known}
    out: list[SpeakerName] = []
    seen = set()

    def make(r, status, why=None, note="", seg=None):
        return SpeakerName(
            name_id="", speaker=str(r.get("speaker", "")), name=str(r.get("name", "")).strip(), basis=str(r.get("basis", "")),
            segment_id=str(r.get("segment_id", "")), start=seg.start if seg else 0.0, quote=str(r.get("quote", "")),
            status=status, reject_reason=why, note=note,
        )

    for r in raw:
        key = (r.get("speaker"), str(r.get("name", "")).lower(), r.get("segment_id"))
        if key in seen:
            continue
        seen.add(key)
        idx_seg = by_id.get(r.get("segment_id"))
        seg = idx_seg[1] if idx_seg else None
        name, quote, speaker, basis = r.get("name"), r.get("quote"), r.get("speaker"), r.get("basis")
        if seg is None:
            out.append(make(r, "rejected", "unknown segment"))
        elif speaker not in labels:
            out.append(make(r, "rejected", "unknown speaker label", seg=seg))
        elif basis not in BASES:
            out.append(make(r, "rejected", f"basis '{basis}' not allowed", seg=seg))
        elif not _valid_name(name):
            out.append(make(r, "rejected", "not a plausible personal name", seg=seg))
        elif not isinstance(quote, str) or quote not in seg.text:
            out.append(make(r, "rejected", "quote not found verbatim in that segment", seg=seg))
        elif not _word_in(quote, name):
            out.append(make(r, "rejected", "the quote does not contain the name", seg=seg))
        elif basis == "self_introduction":
            if seg.speaker == speaker:
                out.append(make(r, "applied", seg=seg, note="said by this speaker about themselves"))
            else:
                out.append(make(r, "rejected", f"claimed self-introduction, but the quote is spoken by {seg.speaker}", seg=seg))
        else:
            i = idx_seg[0]
            nxt = next((s.speaker for s in t.segments[i + 1 : i + 4] if s.speaker != seg.speaker and s.speaker_known), None)
            if seg.speaker == speaker:
                out.append(make(r, "rejected", "a speaker cannot be addressed by name in their own segment", seg=seg))
            else:
                note = "the next speaker is this speaker" if nxt == speaker else f"the next speaker is {nxt or 'unclear'}, not necessarily this speaker"
                out.append(make(r, "suggested", note=note, seg=seg))
    _resolve_conflicts(out)
    for i, n in enumerate(sorted(out, key=lambda n: (n.start, n.name)), 1):
        n.name_id = f"spk_{i:03d}"
    return sorted(out, key=lambda n: n.name_id)


def _same_name(a: str, b: str) -> bool:
    return a.lower() == b.lower() or similarity(a, b) >= 0.85 or a.lower().split()[0] == b.lower().split()[0]


def _resolve_conflicts(names: list[SpeakerName]) -> None:
    """One speaker, one name; one name, one speaker. Contradictions are demoted to suggestions for a human."""
    live = [n for n in names if n.status == "applied"]
    per_speaker: dict[str, list[SpeakerName]] = defaultdict(list)
    for n in live:
        per_speaker[n.speaker].append(n)
    for spk, items in per_speaker.items():
        if any(not _same_name(items[0].name, o.name) for o in items[1:]):
            for n in items:
                n.status, n.note = "suggested", f"conflicting names claimed for {spk}"
    live = [n for n in names if n.status == "applied"]
    for i, a in enumerate(live):
        for b in live[i + 1 :]:
            if a.speaker != b.speaker and _same_name(a.name, b.name):
                for n in (a, b):
                    n.status, n.note = "suggested", f"the same name is claimed for {a.speaker} and {b.speaker}"


def name_is_applied(n: SpeakerName) -> bool:
    if n.status == "rejected":
        return False
    if n.user_decision:
        return n.user_decision == "accept"
    return n.status == "applied"


def effective_names(names: list[SpeakerName], manual: dict[str, str]) -> dict[str, str]:
    """speaker label -> display name. A typed name wins; else the earliest accepted/applied proposal."""
    out: dict[str, str] = {}
    for n in sorted(names, key=lambda n: (n.basis != "self_introduction", n.start)):
        if name_is_applied(n) and n.speaker not in out:
            out[n.speaker] = n.name
    out.update({k: v for k, v in manual.items() if v})
    return out


def apply_names(t: Transcript, mapping: dict[str, str]) -> Transcript:
    segs = [s.model_copy(update={"speaker_name": mapping.get(s.speaker) if s.speaker_known else None}) for s in t.segments]
    return t.model_copy(update={"segments": segs})
