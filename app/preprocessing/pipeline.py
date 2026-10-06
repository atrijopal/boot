"""Phase 1 orchestration: validate -> prepare -> ASR (API) -> normalize -> extract."""
import json
import uuid
from collections.abc import Callable
from pathlib import Path

from app.asr.registry import get_provider
from app.audio.prepare import prepare_audio
from app.audio.validate import validate_file
from app.config.settings import settings
from app.utils import cache as cache_io
from app.preprocessing.llm_candidates import detect_candidates
from app.preprocessing.common import overlaps
from app.preprocessing.context import build_context
from app.preprocessing.datetimes import extract_dates, extract_durations, extract_times
from app.preprocessing.entities import extract_entities
from app.preprocessing.negation import extract_negations
from app.preprocessing.nlp import parse_segments
from app.preprocessing.normalize import normalize_transcript
from app.preprocessing.numbers import extract_numbers
from app.preprocessing.terms import discover_terms
from app.schemas.facts import ProtectedFacts
from app.schemas.result import ProcessingResult
from app.schemas.transcript import Transcript
from app.utils.errors import PipelineError
from app.utils.logging import get_logger, timed

log = get_logger("pipeline")
StatusFn = Callable[[str, str], None]  # (stage, message)


def extract_facts(normalized: Transcript):
    """Deterministic + spaCy extractors (no LLM). Returns (ProtectedFacts, topic_hints)."""
    segs = normalized.segments
    docs = parse_segments([s.text for s in segs])
    facts = ProtectedFacts()
    exclude: dict[str, list[tuple[int, int]]] = {}

    for seg, doc in zip(segs, docs):
        dates, times, durs = extract_dates(seg), extract_times(seg), extract_durations(seg)
        temporal = [(f.char_start, f.char_end) for f in dates + times + durs]
        numbers = [n for n in extract_numbers(seg) if not any(overlaps((n.char_start, n.char_end), t) for t in temporal)]
        ents = extract_entities(seg, doc, temporal)
        facts.dates += dates
        facts.times += times
        facts.durations += durs
        facts.numbers += numbers
        for bucket, items in ents.items():
            getattr(facts, bucket).extend(items)
        facts.negations += extract_negations(seg, doc)

        claimed = temporal + [(n.char_start, n.char_end) for n in numbers]
        # People/places/capitalized names are not "terms". ORG/PRODUCT guesses are NOT excluded:
        # NER labels unknown jargon (and ASR mis-hearings) as ORG, and those are what refinement needs to see.
        for bucket in ("people", "locations", "proper_noun_candidates"):
            claimed += [(i.char_start, i.char_end) for i in ents[bucket]]
        exclude[seg.segment_id] = claimed

    facts.technical_terms, facts.acronyms, hints = discover_terms(segs, docs, exclude, normalized.duration_seconds)
    return facts, hints


def run_phase1(
    path: str | Path,
    job_id: str | None = None,
    provider_name: str | None = None,
    status: StatusFn | None = None,
) -> ProcessingResult:
    job_id = job_id or uuid.uuid4().hex[:12]
    say = status or (lambda stage, msg: None)
    timings: dict[str, float] = {}
    warnings: list[str] = []
    out_dir = Path(settings.outputs_dir) / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    with timed(timings, "total", log):
        say("validating", "Checking the file")
        with timed(timings, "validate", log):
            p = validate_file(path)

        say("preparing", "Decoding and preparing audio")
        with timed(timings, "prepare_audio", log):
            prepared = prepare_audio(p)
        meta = prepared.metadata
        provider = get_provider(provider_name)

        cached = cache_io.load(meta.sha256, provider.name, provider.model)
        if cached:
            say("transcribing", "Reusing cached transcript (no API call)")
            raw = Transcript.model_validate(cached["transcript"])
            raw_payload, n_chunks = cached["raw"], cached.get("chunks", 0)
            timings["asr"] = 0.0
        else:
            say("transcribing", f"Transcribing with {provider.name} ({provider.model})")
            with timed(timings, "asr", log):
                res = provider.transcribe(p, prepared.samples, prepared.sample_rate, lambda m: say("transcribing", m))
            raw, raw_payload, n_chunks = res.transcript, res.raw, res.chunks
            cache_io.store(meta.sha256, provider.name, provider.model, {"transcript": raw.model_dump(), "raw": raw_payload, "chunks": n_chunks})
        meta.chunks = n_chunks
        warnings += raw.notes

        if not raw.segments or not raw.full_text().strip():
            raise PipelineError("NO_SPEECH", "No speech was detected in this recording.")
        if not raw.speakers_known:
            warnings.append("Speaker labels unavailable with this provider; all segments are SPEAKER_UNKNOWN.")

        say("normalizing", "Normalizing transcript")
        with timed(timings, "normalize", log):
            normalized = normalize_transcript(raw)

        say("extracting", "Extracting numbers, names, terms and cues")
        with timed(timings, "extract", log):
            facts, hints = extract_facts(normalized)
            say("extracting", "Labelling questions, proposals, decisions, tasks (LLM)")
            cands, cand_warnings = detect_candidates(normalized)
            for bucket, items in cands.items():
                setattr(facts, bucket, items)
            warnings += cand_warnings
            ctx = build_context(normalized, facts, hints, notes=[])

    result = ProcessingResult(
        job_id=job_id, provider=provider.name, model=raw.model, audio_metadata=meta, raw_transcript=raw,
        normalized_transcript=normalized, meeting_context=ctx, protected_facts=facts,
        timings_seconds=timings, warnings=warnings, cache_hit=bool(cached),
    )
    _save(out_dir, result, raw_payload)
    log.info("job %s done: %d segments, %.1fs audio", job_id, len(raw.segments), meta.duration_seconds)
    return result


def _save(out_dir: Path, r: ProcessingResult, raw_payload: dict) -> None:
    def dump(name: str, obj):
        (out_dir / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    dump("audio_metadata.json", r.audio_metadata.model_dump())
    dump("asr_raw.json", raw_payload)
    dump("raw_transcript.json", r.raw_transcript.model_dump())
    (out_dir / "raw_transcript.txt").write_text(
        "\n".join(f"[{s.start:7.2f}-{s.end:7.2f}] {s.speaker}: {s.text}" for s in r.raw_transcript.segments),
        encoding="utf-8",
    )
    dump("normalized_transcript.json", r.normalized_transcript.model_dump())
    dump("protected_facts.json", r.protected_facts.model_dump())
    dump("meeting_context.json", r.meeting_context.model_dump())
    dump("phase1_result.json", r.model_dump())
