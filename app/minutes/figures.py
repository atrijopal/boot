"""Flag a probable misplaced decimal: the same digits at a different scale close together (e.g. '1250 euro' vs '$12.50')."""
import re

from app.minutes.chunking import mmss
from app.schemas.facts import ProtectedFacts
from app.schemas.minutes import Item

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
WINDOW = 180.0  # seconds


def _sig(token: str) -> str:
    return re.sub(r"\D", "", token).rstrip("0")


def flag_number_mismatches(items: list[Item], facts: ProtectedFacts) -> list[str]:
    warnings: list[str] = []
    for it in items:
        if it.lens != "fact":
            continue
        for tok in NUM.findall(it.attrs["value"]):
            sig = _sig(tok)
            if len(sig) < 3:  # '25' vs '250' is far too common to mean anything
                continue
            try:
                a = float(tok.replace(",", ""))
            except ValueError:
                continue
            for n in facts.numbers:
                if abs(n.start - it.start) > WINDOW:
                    continue
                for other in NUM.findall(n.surface):
                    try:
                        b = float(other.replace(",", ""))
                    except ValueError:
                        continue
                    ratio = max(a, b) / min(a, b) if a and b else 0
                    if a != b and _sig(other) == sig and ratio in (10.0, 100.0, 1000.0):
                        msg = f"possible misplaced decimal: '{tok}' here vs '{n.surface}' at {mmss(n.start)}"
                        if msg not in it.attrs["flags"]:
                            it.attrs["flags"].append(msg)
                            warnings.append(f"{it.item_id}: {msg}. Check the recording.")
    return warnings
