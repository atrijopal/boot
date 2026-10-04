"""People / organizations / places via spaCy NER, plus a capitalization heuristic for missed proper nouns.

No name lists. spaCy's small model gives no confidence, so NER items are "unknown"; heuristic items are "low".
"""
from app.preprocessing.common import make_item
from app.schemas.facts import FactItem
from app.schemas.transcript import TranscriptSegment

LABEL_BUCKET = {
    "PERSON": "people",
    "ORG": "organizations",
    "GPE": "locations",
    "LOC": "locations",
    "FAC": "locations",
    "PRODUCT": "other_entities",
    "WORK_OF_ART": "other_entities",
    "EVENT": "other_entities",
    "NORP": "other_entities",
    "LAW": "other_entities",
}
def extract_entities(seg: TranscriptSegment, doc, skip_spans: list[tuple[int, int]] | None = None) -> dict[str, list[FactItem]]:
    out: dict[str, list[FactItem]] = {"people": [], "organizations": [], "locations": [], "other_entities": [], "proper_noun_candidates": []}
    covered: list[tuple[int, int]] = []
    for ent in doc.ents:
        bucket = LABEL_BUCKET.get(ent.label_)
        if not bucket:
            continue
        covered.append((ent.start_char, ent.end_char))
        out[bucket].append(
            make_item(
                seg, ent.label_, ent.text, ent.start_char, ent.end_char,
                # an ALL-CAPS "entity" is often an acronym/term the NER model guessed at
                confidence="low" if ent.text.isupper() and len(ent.text) > 1 else "unknown",
                source="spacy_ner", extra={"all_caps": True} if ent.text.isupper() else {},
            )
        )

    # Capitalized tokens inside a sentence (not its first word) that NER did not claim.
    i, toks = 0, list(doc)
    while i < len(toks):
        t = toks[i]
        in_ent = any(a <= t.idx < b for a, b in covered) or any(a <= t.idx < b for a, b in (skip_spans or []))  # skip = dates/times
        starts = t.is_sent_start or i == 0
        is_cap = t.text[:1].isupper() and not t.text.isupper() and t.is_alpha and t.text != "I" and t.text[1:] == t.text[1:].lower()  # internal capitals (NAFNet) are terms, not names
               
        if is_cap and not in_ent and (not starts or t.pos_ == "PROPN"):
            j = i
            while j + 1 < len(toks) and toks[j + 1].text[:1].isupper() and toks[j + 1].is_alpha and not toks[j + 1].text.isupper():
                j += 1
            cs, ce = t.idx, toks[j].idx + len(toks[j].text)
            out["proper_noun_candidates"].append(
                make_item(
                    seg, "PROPER_NOUN_CANDIDATE", seg.text[cs:ce], cs, ce, confidence="low",
                    source="capitalization_heuristic", extra={"pos": t.pos_},
                )
            )
            i = j
        i += 1
    return out
