"""Negation cues and their (parser-estimated) scope, from spaCy's dependency parse (`neg` relation).

No word lists: what counts as negation is decided by the parser model. A preprocessing signal, not semantic truth.
"""
from app.preprocessing.common import make_item
from app.schemas.facts import FactItem
from app.schemas.transcript import TranscriptSegment

MAX_SCOPE_TOKENS = 12


def _cue_span(doc, tok) -> tuple[int, int]:
    """Merge a cue with the word it is attached to without a space: "do"+"n't" -> "don't", "can"+"not" -> "cannot"."""
    cs, ce = tok.idx, tok.idx + len(tok.text)
    if tok.i > 0:
        prev = doc[tok.i - 1]
        if prev.idx + len(prev.text) == tok.idx and prev.is_alpha:
            cs = prev.idx
    return cs, ce


def _scope(doc, tok) -> tuple[int, int]:
    sent = tok.sent
    head = tok.head
    if head.i != tok.i:
        toks = [t for t in head.subtree if t.i > tok.i and t.sent == sent and not t.is_punct]
    else:
        toks = []
    if not toks:  # root cue ("No, ...", "Never mind") -> rest of the clause
        toks = [t for t in doc[tok.i + 1 : sent.end] if not t.is_punct]
    toks = toks[:MAX_SCOPE_TOKENS]
    if not toks:
        return tok.idx, tok.idx + len(tok.text)
    return toks[0].idx, toks[-1].idx + len(toks[-1].text)


def extract_negations(seg: TranscriptSegment, doc) -> list[FactItem]:
    out: list[FactItem] = []
    seen: list[tuple[int, int]] = []

    def add(cs: int, ce: int, scope: tuple[int, int], conf: str, source: str):
        if any(cs < b and a < ce for a, b in seen):
            return
        seen.append((cs, ce))
        cue = seg.text[cs:ce]
        out.append(
            make_item(
                seg, "negation", cue.lower(), cs, ce, confidence=conf, source=source,
                extra={
                    "scope": seg.text[scope[0] : scope[1]],
                    "scope_char_start": scope[0],
                    "scope_char_end": scope[1],
                    "emphasis": len(cue) > 1 and cue.isupper(),
                },
            )
        )

    for tok in doc:
        if tok.dep_ == "neg":
            cs, ce = _cue_span(doc, tok)
            add(cs, ce, _scope(doc, tok), "high", "spacy_dep_neg")
    return sorted(out, key=lambda f: f.char_start or 0)
