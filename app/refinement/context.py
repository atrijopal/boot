"""Prompt-side context built from Phase 1 output. Everything here is derived from THIS meeting."""
from app.schemas.facts import ProtectedFacts
from app.schemas.transcript import TranscriptSegment


def term_block(facts: ProtectedFacts, limit: int = 60) -> str:
    acr = [t.term for t in facts.acronyms][:limit]
    terms = [t.term for t in facts.technical_terms][:limit]
    names = sorted({i.value for b in ("people", "organizations", "proper_noun_candidates") for i in getattr(facts, b)})[:limit]
    lines = ["MEETING TERMS (discovered from this transcript by software; may include misheard variants):"]
    lines.append("acronyms: " + (", ".join(acr) or "none"))
    lines.append("terms: " + (", ".join(terms) or "none"))
    lines.append("names: " + (", ".join(names) or "none"))
    return "\n".join(lines)


def segment_line(seg: TranscriptSegment) -> str:
    who = seg.speaker if seg.speaker_known else "speaker unknown"
    return f"[{seg.segment_id}] ({who}) {seg.text}"


def hints_for(seg: TranscriptSegment, facts: ProtectedFacts, diffs: list[dict]) -> str:
    bits = []
    unusual = sorted({t.term for t in facts.technical_terms + facts.acronyms if any(m.segment_id == seg.segment_id for m in t.mentions)
                      and (t.asr_suspect or t.kind == "rare_word")})
    if unusual:
        bits.append("unusual words: " + ", ".join(unusual[:8]))
    for d in diffs[:4]:
        bits.append(f'other ASR heard "{d["secondary"]}" where this has "{d["primary"]}"')
    return "   hints: " + " | ".join(bits) if bits else ""
