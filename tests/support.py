"""A small meeting, a fake speech-to-text provider and a scripted fake model."""
import json

import app.core.pipeline as pl
from app.asr.base import ASRResult
from app.core.llm import prompt_text
from app.core.units import build_units
from app.schemas.transcript import Transcript, TranscriptSegment
from app.utils.errors import PipelineError

MEETING = [
    ("SPEAKER_00", "So we need to decide the selling price, and I think we should go with 25 euro."),
    ("SPEAKER_01", "Agreed, let's go with 25 euro."),
    ("SPEAKER_02", "I'll send the budget report by Friday."),
    ("SPEAKER_00", "Jessie, can you check the supplier list?"),
    ("SPEAKER_03", "Sure. What about the cost, is it 1250 euro?"),
    ("SPEAKER_00", "No, the pee ess en are metric is what we track."),
    ("SPEAKER_01", "Hi everyone, my name is Alima."),
    ("SPEAKER_00", "PSNR went up, but we don't need the LPIPS number."),
]


def make_transcript(rows=MEETING, known=True) -> Transcript:
    segs = [TranscriptSegment(segment_id=f"seg_{i + 1:04d}", start=i * 10.0, end=i * 10.0 + 9, text=t, speaker=sp if known else "SPEAKER_UNKNOWN", speaker_known=known)
            for i, (sp, t) in enumerate(rows)]
    return Transcript(provider="fake", model="fake-asr", duration_seconds=len(segs) * 10.0, speakers_known=known, segments=segs)


def uid(transcript: Transcript, seg_index: int) -> str:
    units, _ = build_units(transcript)
    return next(u.uid for u in units if seg_index in u.seg_idx)


class FakeASR:
    name, model = "fake", "fake-asr"
    supports_diarization = True

    def __init__(self, transcript):
        self.t = transcript

    def transcribe(self, path, samples, sr, progress=None):
        return ASRResult(self.t, {"fake": True}, 0)


class FakeLLM:
    """Scripted answers per prompt. Records every call so tests can assert how many calls and tokens a run used."""

    def __init__(self, transcript):
        self.t = transcript
        self.calls = []
        self.fail = set()
        self.verdict = {}  # statement substring -> verdict fields
        self.by_text = {prompt_text(n): n for n in ("refine", "extract", "review", "speakers", "names", "extract_core", "extract_context", "extract_decisions", "extract_actions", "extract_issues", "extract_details", "extract_discussion", "verify_edits")}

    def __call__(self, system, user, model, effort):
        name = self.by_text[system]
        self.calls.append((name, model, effort, user))
        if name in self.fail:
            raise PipelineError("API_ERROR", f"scripted failure of {name}")
        data = getattr(self, "r_" + name)(user)
        return data, {"model": model, "secs": 0.01, "usage": {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1300}}

    CORE = {"decision", "proposal", "action", "disagreement"}

    def r_extract_core(self, user):
        d = self.r_extract(user)
        return {"items": [i for i in d["items"] if i["t"] in self.CORE]}

    def r_extract_context(self, user):
        d = self.r_extract(user)
        return {"items": [i for i in d["items"] if i["t"] not in self.CORE], "topics": d["topics"], "names": d["names"]}

    def r_extract_decisions(self, user):
        return {"items": [i for i in self.r_extract(user)["items"] if i["t"] in ("decision", "proposal", "disagreement")]}

    def r_extract_actions(self, user):
        return {"items": [i for i in self.r_extract(user)["items"] if i["t"] == "action"]}

    def r_extract_issues(self, user):
        return {"items": [i for i in self.r_extract(user)["items"] if i["t"] in ("question", "risk")]}

    def r_extract_details(self, user):
        d = self.r_extract(user)
        return {"items": [i for i in d["items"] if i["t"] == "fact"], "topics": d["topics"], "names": d["names"]}

    def r_extract_discussion(self, user):
        return {"items": [{"t": "point", "s": "discussed", "x": "The group weighed a selling price of 25 euro.", "q": [[uid(self.t, 0), "I think we should go with 25 euro"]]}]}

    def r_verify_edits(self, user):
        return {"v": [{"i": int(l.split(" | ")[0]), "ok": False} for l in user.split("CANDIDATES:\n")[1].splitlines()]}

    def r_names(self, user):
        return {"people": []}

    def r_speakers(self, user):
        return {"turns": []}

    def r_refine(self, user):
        return {"edits": [{"u": uid(self.t, 5), "find": "pee ess en are", "rep": "PSNR", "k": "acronym"}]}

    def r_extract(self, user):
        t, u = self.t, lambda i: uid(self.t, i)
        return {
            "items": [
                {"t": "proposal", "s": "proposed", "x": "Go with a selling price of 25 euro.", "q": [[u(0), "I think we should go with 25 euro"]]},
                {"t": "decision", "s": "confirmed", "x": "The group agreed on a selling price of 25 euro.", "q": [[u(1), "Agreed, let's go with 25 euro"]]},
                {"t": "action", "s": "committed", "x": "Send the budget report.", "o": "first", "ou": u(2), "d": "by Friday", "q": [[u(2), "I'll send the budget report by Friday"]]},
                {"t": "action", "s": "assigned", "x": "Check the supplier list.", "o": "named", "on": "Jessie", "q": [[u(3), "Jessie, can you check the supplier list"]]},
                {"t": "question", "s": "open", "x": "Is the cost 1250 euro?", "q": [[u(4), "What about the cost, is it 1250 euro?"]]},
                {"t": "fact", "s": "stated", "x": "The selling price is 25 euro.", "l": "selling price", "v": "25 euro", "q": [[u(1), "let's go with 25 euro"]]},
            ],
            "topics": [{"n": "Price and tasks", "a": u(0), "b": u(4)}, {"n": "Metrics", "a": u(5), "b": u(7)}],
            "names": [{"t": next((tag for tag, sp in build_units(t)[1].items() if sp == "SPEAKER_01"), "?"), "n": "Alima", "u": u(6), "q": "my name is Alima"}],
        }

    def r_review(self, user):
        body = [json.loads(l) for l in user.split("RECORDS:\n")[1].splitlines()]
        out = []
        for r in body:
            if "c" not in r:
                continue
            v = {"n": r["n"], "ok": True, "st": None, "own": True if r.get("o") else None, "dl": True if r.get("d") else None}
            for sub, fields in self.verdict.items():
                if sub in r["x"]:
                    v.update(fields)
            out.append(v)
        first = lambda kind: next((r["n"] for r in body if r["k"] == kind), body[0]["n"])
        spk = {}
        for r in body:
            if r.get("w") and r["w"] not in spk:
                spk[r["w"]] = r["n"]
        speakers = [{"w": w, "b": [{"x": "Took part in the discussion.", "i": [n]}]} for w, n in spk.items()]
        return {"v": out, "speakers": speakers, "head": {"x": "The meeting settled a selling price and assigned follow-up work.", "i": [first("decision")]}, "sum": [{"x": "The group settled on a selling price of 25 euro.", "i": [first("decision")]}, {"x": "A budget report is due by Friday.", "i": [first("action")]}],
                "ctx": [{"n": first("decision"), "x": "It followed the proposal to go with a selling price.", "i": [first("decision")]}],
                "topics": [{"t": 0, "o": "decided", "b": [{"x": "A price of 25 euro was agreed.", "i": [first("decision")]}]}]}


def install(monkeypatch, transcript=None, known=True):
    t = transcript or make_transcript(known=known)
    fake = FakeLLM(t)
    import app.core.llm as llm
    monkeypatch.setattr(llm, "_call_model", fake)
    monkeypatch.setattr(pl, "get_provider", lambda name=None: FakeASR(t))
    return fake
