"""Technical-term discovery, per meeting. No domain vocabulary is hardcoded.

Signals (all language-level, not topic-level):
  - acronym / mixed-case / alphanumeric / spelled-out-letter tokens
  - words that are rare in general English (wordfreq zipf)
  - recurring noun phrases scored with TF-IDF over time windows of THIS meeting
  - ASR-suspect flag: rare/odd token in a low-confidence region of the ASR output
"""
import math
import re
from collections import defaultdict

from sklearn.feature_extraction.text import TfidfVectorizer
from wordfreq import zipf_frequency

from app.config.settings import settings
from app.preprocessing.common import WORD_RE, overlaps, span_times
from app.schemas.facts import Mention, TermFact, TopicHint
from app.schemas.transcript import TranscriptSegment

ACRONYM_PATTERNS = [
    ("spelled_acronym", re.compile(r"\b(?:[A-Z]-){2,}[A-Z]\b|\b(?:[A-Z]\.){2,}|\b(?:[A-Z] ){2,}[A-Z]\b"), "medium"),
    ("acronym", re.compile(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*\b|\b[A-Z]{2,}[a-z][A-Za-z0-9]*\b"), "medium"),  # PyTorch, NAFNet
    ("acronym", re.compile(r"\b[A-Z]{2,}[0-9]*s?\b"), "medium"),  # GPU, GPUs, BERT4
    ("acronym", re.compile(r"\b[A-Za-z]+-?\d+[A-Za-z0-9]*\b"), "medium"),  # GPT-4, H100, wav2vec
]


def _spelled_value(s: str) -> str:
    return re.sub(r"[^A-Z]", "", s)


def _word_conf(seg: TranscriptSegment, token: str) -> float | None:
    t = token.lower().strip(".,?!;:")
    for w in seg.words:
        if w.confidence is not None and w.text.lower().strip(".,?!;:") == t:
            return w.confidence
    return None


def _suspect(seg: TranscriptSegment, token: str) -> str | None:
    c = _word_conf(seg, token)
    if c is not None and c < settings.asr_suspect_word_conf:
        return f"word confidence {c:.2f}"
    if c is None and seg.avg_logprob is not None and seg.avg_logprob < settings.asr_suspect_logprob:
        return f"low-likelihood segment (avg_logprob {seg.avg_logprob:.2f})"
    return None


def _phrase_candidates(doc, excl: list[tuple[int, int]]) -> list[tuple[str, int, int]]:
    out = []
    for chunk in doc.noun_chunks:
        toks = [t for t in chunk]
        while toks and (toks[0].pos_ in ("DET", "PRON", "NUM", "ADV") or toks[0].is_stop or toks[0].like_num):
            toks.pop(0)
        while toks and (toks[-1].is_stop or toks[-1].pos_ in ("PRON", "NUM")):
            toks.pop()
        if not toks or not any(t.pos_ in ("NOUN", "PROPN") for t in toks) or len(toks) > 4:
            continue
        cs, ce = toks[0].idx, toks[-1].idx + len(toks[-1].text)
        if any(overlaps((cs, ce), e) for e in excl):
            continue
        text = re.sub(r"\s+", " ", doc.text[cs:ce].lower())
        if not re.search(r"[a-z]", text) or any(not t.text.replace("-", "").isalpha() for t in toks if not t.is_punct or t.text != "-"):
            continue
        if len(toks) == 1 and zipf_frequency(text, "en") >= 4.0:
            continue  # a single common word is not jargon
        out.append((text, cs, ce))
    return out


def discover_terms(
    segments: list[TranscriptSegment],
    docs: list,
    exclude: dict[str, list[tuple[int, int]]],
    duration: float,
) -> tuple[list[TermFact], list[TermFact], list[TopicHint]]:
    """Returns (technical_terms, acronyms, topic_hints). `exclude` = per-segment spans already claimed
    by numbers/dates/times/names, so they are not re-reported as terms."""
    acronyms: dict[str, TermFact] = {}
    rare: dict[str, TermFact] = {}
    phrases: dict[str, TermFact] = {}
    win_len = max(60.0, duration / 8) if duration else 60.0
    window_docs: dict[int, list[str]] = defaultdict(list)

    def add(store, key, kind, source, conf, seg, cs, ce, zipf=None, suspect=None):
        t0, t1 = span_times(seg, cs, ce)
        tf = store.get(key)
        if tf is None:
            tf = store[key] = TermFact(term=key, kind=kind, sources=[source], count=0, confidence=conf, zipf=zipf)
        tf.count += 1
        if suspect and not tf.asr_suspect:
            tf.asr_suspect, tf.asr_suspect_reason = True, suspect
        if len(tf.mentions) < 20:
            tf.mentions.append(Mention(segment_id=seg.segment_id, start=round(t0, 3), end=round(t1, 3), char_start=cs, char_end=ce))

    for seg, doc in zip(segments, docs):
        excl = list(exclude.get(seg.segment_id, []))
        text = seg.text
        claimed: list[tuple[int, int]] = []

        # 1) acronym-like tokens (earlier patterns win overlaps)
        for kind, rx, conf in ACRONYM_PATTERNS:
            for m in rx.finditer(text):
                span = (m.start(), m.end())
                tok = m.group()
                if any(overlaps(span, c) for c in claimed) or any(overlaps(span, e) for e in excl):
                    continue
                if len(tok) < 2:
                    continue
                # ALL-CAPS that is an ordinary English word ("NOT", "OK") is emphasis, not an acronym (statistical test)
                if tok.isupper() and zipf_frequency(tok.lower(), "en") >= (5.0 if len(tok) == 2 else 4.5):
                    continue
                claimed.append(span)
                key = _spelled_value(tok) if kind == "spelled_acronym" else tok
                add(acronyms, key, kind, "pattern", conf, seg, m.start(), m.end(), suspect=_suspect(seg, tok))

        # 2) rare words in general English
        for m in WORD_RE.finditer(text):
            tok = m.group().strip("'’-")
            span = (m.start(), m.end())
            if len(tok) < 4 or any(overlaps(span, c) for c in claimed) or any(overlaps(span, e) for e in excl):
                continue
            if tok.endswith(("'s", "’s")):
                tok = tok[:-2]
            parts = [p for p in tok.lower().split("-") if p]
            z = min(zipf_frequency(p, "en") for p in parts) if parts else 9.0
            if z < settings.rare_zipf:
                add(rare, tok.lower(), "rare_word", "wordfreq_rarity", "low", seg, m.start(), m.end(), zipf=round(z, 2), suspect=_suspect(seg, tok))

        # 3) recurring noun phrases (candidates; scored below)
        cands = _phrase_candidates(doc, excl + claimed)
        window = int(seg.start // win_len)
        window_docs[window].extend(c[0] for c in cands)
        for text_p, cs, ce in cands:
            add(phrases, text_p, "keyphrase", "tfidf_noun_phrase", "low", seg, cs, ce)

    # confidence: repetition raises it; a single mention stays low
    for tf in rare.values():
        if tf.count >= 2:
            tf.confidence = "medium"

    # TF-IDF over time windows of this meeting
    topic_hints: list[TopicHint] = []
    keep: list[TermFact] = []
    wids = sorted(window_docs)
    if wids and phrases:
        vec = TfidfVectorizer(analyzer=lambda x: x, sublinear_tf=True)
        try:
            X = vec.fit_transform([window_docs[w] for w in wids])
            vocab = vec.get_feature_names_out()
            scores = X.sum(axis=0).A1
            score_of = dict(zip(vocab, scores))
            ranked = sorted((p for p in phrases.values() if p.count >= 2), key=lambda p: -score_of.get(p.term, 0))
            for p in ranked[:40]:
                p.confidence = "medium" if p.count >= 3 else "low"
                keep.append(p)
            for row, w in enumerate(wids):
                r = X[row].toarray().ravel()
                top = [vocab[i] for i in r.argsort()[::-1][:5] if r[i] > 0]
                if top:
                    topic_hints.append(TopicHint(start=round(w * win_len, 1), end=round(min((w + 1) * win_len, duration), 1), terms=top))
        except ValueError:
            pass  # empty vocabulary

    merged: dict[str, TermFact] = {}
    for tf in list(rare.values()) + keep:
        if tf.term in merged:
            m = merged[tf.term]
            m.sources = sorted(set(m.sources + tf.sources))
            m.count = max(m.count, tf.count)
            m.asr_suspect = m.asr_suspect or tf.asr_suspect
            m.asr_suspect_reason = m.asr_suspect_reason or tf.asr_suspect_reason
        else:
            merged[tf.term] = tf
    terms = sorted(merged.values(), key=lambda t: (-t.count, t.term))
    acr = sorted(acronyms.values(), key=lambda t: (-t.count, t.term))
    return terms, acr, topic_hints
