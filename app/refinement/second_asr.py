"""Optional second opinion from the *other* ASR provider, used as evidence (never as the text itself)."""
import difflib
import re
from pathlib import Path

from app.asr.registry import get_provider
from app.audio.prepare import prepare_audio
from app.config.settings import settings
from app.schemas.transcript import Transcript, TranscriptSegment
from app.utils import cache
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("second_asr")
_TOK = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")


def other_provider(primary: str) -> str | None:
    if primary == "groq" and settings.deepgram_api_key:
        return "deepgram"
    if primary == "deepgram" and settings.groq_api_key:
        return "groq"
    return None


def run_second_asr(primary: str, audio_path: str, sha256: str) -> tuple[Transcript | None, str | None, str | None]:
    """Returns (transcript, provider_name, warning)."""
    name = other_provider(primary)
    if not name:
        return None, None, "Second-ASR check skipped: no key for a second provider."
    p = Path(audio_path)
    if not audio_path or not p.is_file():
        return None, name, "Second-ASR check skipped: the original audio file is no longer available."
    try:
        prov = get_provider(name)
        hit = cache.load(sha256, prov.name, prov.model)
        if hit:
            return Transcript.model_validate(hit["transcript"]), name, None
        prepared = prepare_audio(p)
        res = prov.transcribe(p, prepared.samples, prepared.sample_rate)
        cache.store(sha256, prov.name, prov.model, {"transcript": res.transcript.model_dump(), "raw": res.raw, "chunks": res.chunks})
        return res.transcript, name, None
    except PipelineError as e:
        log.warning("second ASR failed: %s", e.code)
        return None, name, f"Second-ASR check unavailable ({e.message})."


def heard_by_segment(primary: Transcript, secondary: Transcript) -> dict[str, str]:
    """What the second ASR heard during each primary segment (by word midpoints; falls back to segment overlap)."""
    words = [w for s in secondary.segments for w in s.words]
    out: dict[str, str] = {}
    for seg in primary.segments:
        if words:
            got = [w.text for w in words if seg.start - 0.15 <= (w.start + w.end) / 2 <= seg.end + 0.15]
        else:
            got = [s.text for s in secondary.segments if s.start < seg.end and s.end > seg.start]
        out[seg.segment_id] = " ".join(got)
    return out


def disagreements(seg: TranscriptSegment, heard: str) -> list[dict]:
    """Phrases where the two ASRs differ (replace blocks only), with char spans in the primary segment text."""
    if not heard:
        return []
    a = [(m.group().lower(), m.start(), m.end()) for m in _TOK.finditer(seg.text)]
    b = [m.group().lower() for m in _TOK.finditer(heard)]
    if not a or not b:
        return []
    sm = difflib.SequenceMatcher(None, [t[0] for t in a], b, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace" and i2 - i1 <= 6 and j2 - j1 <= 6:
            cs, ce = a[i1][1], a[i2 - 1][2]
            out.append({"primary": seg.text[cs:ce], "secondary": " ".join(b[j1:j2]), "char_start": cs, "char_end": ce})
    return out
