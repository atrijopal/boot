"""Product wording for warnings the pipeline writes for developers. Anything a user can read goes through `plain`: no model, service or token talk."""
import re

RULES = [
    (r"^(\S+): (\d+) of (\d+) proposed record\(s\) dropped by the quote/number checks", lambda m: f"{m[2]} of {m[3]} items the analysis proposed were left out because the recording does not support them word for word."),
    (r"^(\d+) decision/action record\(s\) did not get a model verdict.*", lambda m: f"{m[1]} decision or action item(s) could not be double-checked automatically; they rest on exact quotes from the recording."),
    (r"^Extraction (\S+) could not run.*", lambda m: "Part of the analysis did not run, so some items may be missing from the record."),
    (r"^Window (\S+) could not be analysed.*", lambda m: "Part of the recording could not be analysed, so items from that part may be missing."),
    (r"^The review step did not run.*", lambda m: "The final check did not run: items rest on exact quotes from the recording and the summary lists them directly."),
    (r"^The model's summary sentences did not pass checking.*", lambda m: "The written summary could not be verified against the record, so the verified items are listed instead."),
    (r"^Refinement of (\S+) did not run.*", lambda m: "Part of the transcript could not be checked for corrections and is shown as heard."),
    (r"^The second check of the proposed corrections did not run.*", lambda m: "The second check of the proposed corrections did not run, so they stay as suggestions for you to decide."),
    (r"^Finding the speakers' names in (\S+) did not run.*", lambda m: "Names could not be looked for in part of the recording."),
    (r"^Speaker labels were inferred.*", lambda m: "Speaker labels were worked out from the text, not from voices, and can be wrong."),
]


def plain(text: str) -> str:
    t = " ".join(str(text).split())
    for rx, fn in RULES:
        m = re.match(rx, t)
        if m:
            return fn(m)
    t = re.sub(r"\s*\([A-Z_]{3,}:[^)]*\)", "", t)  # "(API_ERROR: ...)" codes
    t = re.sub(r"\bthe model's\b", "the", t, flags=re.I)
    t = re.sub(r"\bmodel(s)?\b", r"processing step\1", t, flags=re.I)
    return t
