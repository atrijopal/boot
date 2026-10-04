"""Numbers: digits, %, money, multipliers, versions, ranges, units, and spoken numbers.

Every item keeps the original surface string; `value` is a normalized numeric string.
"""
import re

from app.preprocessing.common import make_item, resolve_overlaps
from app.schemas.facts import FactItem
from app.schemas.transcript import TranscriptSegment

NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
CURRENCY = r"[$€£₹¥]"

# (name, regex, priority, confidence)  lower priority number wins ties
PATTERNS = [
    ("version", re.compile(r"\b[vV]\d+(?:\.\d+)*\b|\b\d+\.\d+\.\d+(?:\.\d+)*\b"), 0, "high"),
    ("money", re.compile(rf"{CURRENCY}\s?{NUM}(?:\s?(?:k|K|m|M|bn|million|billion|thousand|lakh|crore)\b)?"), 1, "high"),
    (
        "money",
        re.compile(rf"\b{NUM}\s?(?:dollars?|bucks|euros?|pounds?|rupees|USD|INR|EUR|GBP)\b", re.I),
        1,
        "high",
    ),
    ("percent", re.compile(rf"\b{NUM}\s?(?:%|percent\b|per cent\b)|{NUM}%"), 2, "high"),
    ("multiplier", re.compile(rf"\b{NUM}\s?[x×](?![A-Za-z0-9])"), 3, "high"),
    ("range", re.compile(rf"\b{NUM}\s?[-–—]\s?{NUM}\b"), 4, "high"),
    ("range", re.compile(rf"\b{NUM}\s+to\s+{NUM}\b", re.I), 4, "medium"),
    ("number", re.compile(rf"(?<![\w.]){NUM}(?![\w])"), 9, "high"),
]

UNIT_RE = re.compile(r"\s+([A-Za-z%]+)")

ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
DIGIT_WORDS = {k: v for k, v in ONES.items() if v < 10}
TOKEN_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
# words after a lone "one" that make it a quantity rather than a pronoun ("one of the...")
QUANTITY_FOLLOWERS = {"percent", "dollars", "hundred", "thousand", "million", "billion", "times", "x"}


def _parse_spoken(seg_text: str) -> list[tuple[int, int, str, str]]:
    """Find runs of number words. Returns (char_start, char_end, value, confidence)."""
    toks = [(m.group().lower(), m.start(), m.end()) for m in TOKEN_RE.finditer(seg_text)]
    # split hyphenated number words ("twenty-five") into parts, keeping char positions
    flat: list[tuple[str, int, int]] = []
    for t, a, b in toks:
        if "-" in t and all(p in ONES or p in TENS for p in t.split("-")):
            pos = a
            for p in t.split("-"):
                flat.append((p, pos, pos + len(p)))
                pos += len(p) + 1
        else:
            flat.append((t, a, b))

    out = []
    i = 0
    while i < len(flat):
        w = flat[i][0]
        if w not in ONES and w not in TENS:
            i += 1
            continue
        total, current, last = 0, 0, None  # last: category of previous token in the run
        j = i
        run_end = i
        while j < len(flat):
            w, a, b = flat[j]
            gap_ok = j == i or (a - flat[j - 1][2]) <= 2  # tokens separated by at most " "/"-"
            if not gap_ok:
                break
            if w in TENS:
                if last in ("tens", "ones", "teens", "tens_ones"):
                    break  # "twenty twenty", "six twenty": a new number starts
                current += TENS[w]
                last = "tens"
            elif w in ONES:
                v = ONES[w]
                if last in ("ones", "teens", "tens_ones"):
                    break
                if last == "tens":
                    if v >= 10:
                        break
                    current += v
                    last = "tens_ones"
                else:
                    current += v
                    last = "teens" if v >= 10 else "ones"
            elif w == "hundred" and last is not None:
                current = max(current, 1) * 100
                last = "hundred"
            elif w in SCALES and last is not None:
                total += max(current, 1) * SCALES[w]
                current = 0
                last = "scale"
            elif w == "and" and last in ("hundred", "scale") and j + 1 < len(flat) and (flat[j + 1][0] in ONES or flat[j + 1][0] in TENS):
                j += 1
                continue
            else:
                break
            run_end = j
            j += 1
        value = total + current
        cs, ce = flat[i][1], flat[run_end][2]
        text_val = str(value)
        # "point" + digit words -> decimals ("eighty one point seven three")
        k = run_end + 1
        if k < len(flat) and flat[k][0] == "point" and (flat[k][1] - flat[run_end][2]) <= 2:
            digits = []
            m = k + 1
            while m < len(flat) and flat[m][0] in DIGIT_WORDS and (flat[m][1] - flat[m - 1][2]) <= 2:
                digits.append(str(DIGIT_WORDS[flat[m][0]]))
                m += 1
            if digits:
                text_val = f"{value}." + "".join(digits)
                ce = flat[m - 1][2]
                run_end = m - 1
        single_one = run_end == i and flat[i][0] == "one"
        nxt = flat[run_end + 1][0] if run_end + 1 < len(flat) else ""
        if not (single_one and nxt not in QUANTITY_FOLLOWERS):
            out.append((cs, ce, text_val, "medium"))
        i = run_end + 1
    return out


def extract_numbers(seg: TranscriptSegment) -> list[FactItem]:
    text = seg.text
    spans: list[tuple[int, int, int, object]] = []
    for name, rx, prio, conf in PATTERNS:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), prio, (name, conf, "regex")))
    for cs, ce, val, conf in _parse_spoken(text):
        # spoken number followed by "percent"/"times" gets absorbed into one span
        tail = re.match(r"\s+(percent|per cent|times|x)\b", text[ce:], re.I)
        kind = "spoken_number"
        if tail:
            ce += tail.end()
            kind = "spoken_percent" if "percent" in tail.group(1).lower() or "per" in tail.group(1).lower() else "spoken_multiplier"
        spans.append((cs, ce, 5, (kind, conf, "spoken_number_parser", val)))

    items: list[FactItem] = []
    for cs, ce, _prio, payload in resolve_overlaps(spans):
        kind, conf, source = payload[0], payload[1], payload[2]
        surface = text[cs:ce]
        extra: dict = {"kind": kind}
        if kind.startswith("spoken"):
            value = payload[3]
            extra["parsed_value"] = value
        else:
            if kind in ("number", "percent", "multiplier"):
                value = re.sub(r"[^\d.]", "", surface.replace(",", ""))  # "81.73%" -> "81.73"
            else:
                value = surface.strip()  # money / range / version keep their exact form
        if kind == "number":
            u = UNIT_RE.match(text[ce:])
            if u:
                extra["unit_candidate"] = u.group(1)
        items.append(make_item(seg, "number", value, cs, ce, confidence=conf, source=source, extra=extra))
    return items
