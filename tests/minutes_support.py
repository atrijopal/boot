"""Shared helpers for Phase 3 tests: a small meeting, a saved job on disk, and a scripted fake LLM."""
import json
import re
import threading
import time
from pathlib import Path

from app.config.settings import settings
from app.minutes.llm import PROMPT_NAMES, prompt_text
from app.preprocessing.context import build_context
from app.preprocessing.pipeline import extract_facts
from app.schemas.audio import AudioMetadata
from app.schemas.refinement import InvariantReport, RefinementResult
from app.schemas.result import ProcessingResult
from app.schemas.transcript import Transcript, TranscriptSegment
from app.utils.errors import PipelineError

MEETING = [
    ("SPEAKER_00", "Rose", "So we need to decide the selling price, and I think we should go with 25 euro."),
    ("SPEAKER_01", "Alima", "Agreed, let's go with 25 euro."),
    ("SPEAKER_02", "Ian", "I'll send the budget report by Friday."),
    ("SPEAKER_00", "Rose", "Jessie, can you check the supplier list?"),
    ("SPEAKER_03", "Jessie", "Sure. What about the cost, is it 1250 euro?"),
    ("SPEAKER_00", "Rose", "No, it should be $12.50."),
    ("SPEAKER_01", "Alima", "The risk is that the supplier might deliver late."),
    ("SPEAKER_02", "Ian", "I don't think 25 euro is right. It is too high."),
    ("SPEAKER_00", "Rose", "I disagree, 25 euro is fine."),
    ("SPEAKER_01", "Alima", "Okay, thanks everyone."),
]


def transcript(rows=MEETING) -> Transcript:
    segs = [TranscriptSegment(segment_id=f"seg_{i + 1:04d}", start=i * 10.0, end=i * 10.0 + 9, text=t, speaker=spk, speaker_known=True, speaker_name=name)
            for i, (spk, name, t) in enumerate(rows)]
    return Transcript(provider="t", model="t", kind="refined", duration_seconds=len(segs) * 10.0, speakers_known=True, segments=segs)


def make_job(job_id="m1", rows=MEETING) -> str:
    refined = transcript(rows)
    norm = refined.model_copy(update={"kind": "normalized"})
    facts, hints = extract_facts(norm)
    meta = AudioMetadata(original_file="a.wav", format="wav", file_size_bytes=1, sha256="z" * 64, duration_seconds=refined.duration_seconds,
                         original_sample_rate=16000, original_channels=1, processed_sample_rate=16000, processed_channels=1, rms=0.1, peak=0.5, clipped_fraction=0)
    p1 = ProcessingResult(job_id=job_id, provider="deepgram", model="nova-3", audio_metadata=meta, raw_transcript=norm, normalized_transcript=norm,
                          meeting_context=build_context(norm, facts, hints, []), protected_facts=facts)
    ref = RefinementResult(job_id=job_id, refine_model="x", verify_model="y", refined_transcript=refined,
                           invariants=InvariantReport(segment_count_unchanged=True, ids_times_speakers_unchanged=True, numbers_unchanged=True,
                                                      dates_times_unchanged=True, negations_unchanged=True))
    d = Path(settings.outputs_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "phase1_result.json").write_text(p1.model_dump_json())
    (d / "refinement.json").write_text(ref.model_dump_json())
    return job_id


def ev(seg, quote):
    return {"segment_id": f"seg_{seg:04d}", "quote": quote}


class FakeLLM:
    """Answers every Phase 3 prompt from a script. Override r_<prompt> or set .fail / .verdict_overrides in a test."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, str]] = []
        self.fail: set[str] = set()
        self.verdict_overrides: dict[str, dict] = {}  # substring of statement -> fields to set on its verdict
        self.claim_unsupported: set[str] = set()  # substrings of claim text to mark unsupported
        self.by_text = {prompt_text(n): n for n in PROMPT_NAMES}
        self.lock = threading.Lock()
        self.active = self.max_active = 0
        self.delay = 0.0

    def __call__(self, system, user, model, effort):
        name = self.by_text[system]
        with self.lock:
            self.calls.append((name, model, effort, user))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if name in self.fail:
                raise PipelineError("API_ERROR", f"scripted failure of {name}")
            data = getattr(self, "r_" + name)(user)
        finally:
            with self.lock:
                self.active -= 1
        return data, {"model": model, "secs": 0.01, "retries": 0, "rate_limited": 0, "effort_fallback": False,
                      "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)

    # ---- scripted answers ------------------------------------------------------------------
    def r_extract_agreements(self, user):
        return {"items": [
            {"lens": "proposal", "text": "Go with a selling price of 25 euro.", "status": "proposed", "evidence": [ev(1, "I think we should go with 25 euro")]},
            {"lens": "decision", "text": "The group agreed to a selling price of 25 euro.", "status": "confirmed",
             "evidence": [ev(2, "Agreed, let's go with 25 euro"), ev(1, "go with 25 euro")]},
            {"lens": "disagreement", "text": "Ian thinks 25 euro is too high while Rose thinks it is fine.", "status": "unresolved",
             "evidence": [ev(8, "I don't think 25 euro is right"), ev(9, "25 euro is fine")]},
        ]}

    def r_extract_actions(self, user):
        return {"items": [
            {"lens": "action", "text": "Send the budget report.", "status": "committed", "owner_basis": "first_person", "owner_segment_id": "seg_0003",
             "owner_text": None, "deadline_text": "by Friday", "evidence": [ev(3, "I'll send the budget report by Friday")]},
            {"lens": "action", "text": "Check the supplier list.", "status": "assigned", "owner_basis": "named_in_quote", "owner_segment_id": None,
             "owner_text": "Jessie", "deadline_text": None, "evidence": [ev(4, "Jessie, can you check the supplier list")]},
        ]}

    def r_extract_questions_risks(self, user):
        return {"items": [
            {"lens": "question", "text": "Is the cost 1250 euro?", "status": "open", "evidence": [ev(5, "What about the cost, is it 1250 euro?")]},
            {"lens": "risk", "text": "The supplier might deliver late.", "status": "raised", "evidence": [ev(7, "the supplier might deliver late")]},
        ]}

    def r_extract_facts(self, user):
        return {"items": [
            {"lens": "fact", "label": "selling price", "value": "25 euro", "text": "The selling price is 25 euro.", "status": "stated", "evidence": [ev(2, "let's go with 25 euro")]},
            {"lens": "fact", "label": "cost", "value": "1250 euro", "text": "The cost was stated as 1250 euro.", "status": "stated", "evidence": [ev(5, "is it 1250 euro")]},
        ]}

    def r_extract_all(self, user):
        items = []
        for fn in (self.r_extract_agreements, self.r_extract_actions, self.r_extract_questions_risks, self.r_extract_facts):
            items += fn(user)["items"]
        return {"items": items}

    def r_verify_items(self, user):
        out = []
        for rec in self._records(user):
            ok = bool(rec.get("status_meaning"))  # like the real verifier: a label with no stated meaning cannot be confirmed
            v = {"id": rec["id"], "supported": True, "status_ok": ok, "better_status": None, "owner_ok": None if not rec.get("owner") else True,
                 "deadline_ok": None if not rec.get("deadline") else True, "reason": "ok"}
            for sub, fields in self.verdict_overrides.items():
                if sub in rec["statement"]:
                    v.update(fields)
            out.append(v)
        return {"verdicts": out}

    @staticmethod
    def _records(user):
        body = user.split("<records>")[1].split("</records>")[0].strip()
        return [json.loads(l) for l in body.splitlines()]

    def r_reconcile(self, user):
        body = [json.loads(l) for l in user.split("<items>")[1].split("</items>")[0].strip().splitlines()]
        dec = next((i for i in body if i["lens"] == "decision"), None)
        prp = next((i for i in body if i["lens"] == "proposal"), None)
        if dec and prp:
            return {"relations": [{"source": dec["id"], "target": prp["id"], "kind": "adopts", "segment_id": "seg_0002", "quote": "let's go with 25 euro"}]}
        return {"relations": []}

    def r_topics(self, user):
        return {"topics": [{"title": "Selling price and tasks", "start_segment": "seg_0001", "end_segment": "seg_0006"},
                           {"title": "Risks and price disagreement", "start_segment": "seg_0007", "end_segment": "seg_0010"}]}

    def r_topic_summary(self, user):
        segs = re.findall(r"\[(seg_\d+)\]", user)
        items = [json.loads(l) for l in user.split("<items>")[1].split("</items>")[0].strip().splitlines() if l.strip()]
        dec = next((i for i in items if i["lens"] == "decision"), None)
        if dec:
            return {"claims": [{"text": "The group agreed on a selling price of 25 euro.", "segment_ids": segs[:2], "item_ids": [dec["id"]]}]}
        return {"claims": [{"text": "A supplier delay was raised as a risk and the price was disputed.", "segment_ids": segs[:2], "item_ids": []}]}

    def r_verify_claims(self, user):
        body = [json.loads(l) for l in user.split("<claims>")[1].split("</claims>")[0].strip().splitlines()]
        return {"verdicts": [{"id": c["id"], "supported": not any(s in c["claim"] for s in self.claim_unsupported),
                              "reason": "adds a number" if any(s in c["claim"] for s in self.claim_unsupported) else "ok"} for c in body]}

    def r_executive(self, user):
        items = [json.loads(l) for l in user.split("<items>")[1].split("</items>")[0].strip().splitlines() if l.strip()]
        dec = next((i for i in items if i["lens"] == "decision"), None)
        return {"claims": [{"text": "The meeting settled on a selling price of 25 euro.", "item_ids": [dec["id"]] if dec else [], "topic_ids": ["t01"]}]}
