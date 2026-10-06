"""Measure the refinement stage on a hand-written fixture with injected ASR errors.

python scripts/eval_refinement.py --refine-model openai/gpt-oss-20b --refine-model openai/gpt-oss-120b [--no-verify]
Reports only what is measured here (small fixture: treat as indicative, not as a general accuracy figure).
Second-ASR evidence is off (no audio), so support comes from the transcript itself and the Phase-1 term list.
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings  # noqa: E402
from app.preprocessing.context import build_context  # noqa: E402
from app.preprocessing.pipeline import extract_facts  # noqa: E402
from app.refinement import pipeline as rp  # noqa: E402
from app.refinement.apply import is_applied  # noqa: E402
from app.schemas.audio import AudioMetadata  # noqa: E402
from app.schemas.result import ProcessingResult  # noqa: E402
from app.schemas.transcript import Transcript, TranscriptSegment  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--refine-model", action="append")
ap.add_argument("--verify-model", default=None)
ap.add_argument("--effort", default=None, help="refiner reasoning effort: low|medium|high")
ap.add_argument("--no-verify", action="store_true")
ap.add_argument("--data", default=str(Path(__file__).parent / "eval_data" / "refine_eval.json"))
ap.add_argument("--list", action="store_true", help="print every edit")
args = ap.parse_args()

settings.cache_enabled = False  # honest latency
settings.refine_use_second_asr = False
settings.verify_enabled = not args.no_verify
if args.effort:
    settings.refine_reasoning_effort = args.effort
if args.verify_model:
    settings.verify_model = args.verify_model
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
data = json.loads(Path(args.data).read_text(encoding="utf-8"))


def make_job(p) -> str:
    jid = f"eval_{p['id']}"
    segs = [TranscriptSegment(segment_id=f"seg_{i + 1:04d}", start=i * 6.0, end=i * 6.0 + 5.5, text=t) for i, t in enumerate(p["segments"])]
    t = Transcript(provider="eval", model="eval", kind="normalized", duration_seconds=len(segs) * 6.0, segments=segs)
    facts, hints = extract_facts(t)
    meta = AudioMetadata(original_file="eval", format="txt", file_size_bytes=0, sha256="0" * 64, duration_seconds=len(segs) * 6.0, original_sample_rate=16000,
                         original_channels=1, processed_sample_rate=16000, processed_channels=1, rms=0.1, peak=0.5, clipped_fraction=0)
    res = ProcessingResult(job_id=jid, provider="eval", model="eval", audio_metadata=meta, raw_transcript=t, normalized_transcript=t,
                           meeting_context=build_context(t, facts, hints, []), protected_facts=facts)
    d = Path(settings.outputs_dir) / jid
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "phase1_result.json").write_text(res.model_dump_json(), encoding="utf-8")
    return jid


report = []
for model in args.refine_model or [settings.refine_model]:
    settings.refine_model = model
    m = dict(gold=0, fixed_auto=0, fixed_suggested=0, missed=0, edits=0, applied=0, false_applied=0, false_suggested=0,
             blocked=0, verifier_vetoes=0, invariant_violations=0, latency=0.0, errors=0)
    for p in data["passages"]:
        jid = make_job(p)
        t0 = time.perf_counter()
        try:
            res = rp.run_refinement(jid)
        except Exception as e:  # report, don't hide
            m["errors"] += 1
            print(f"  [{model}] {p['id']}: ERROR {e}")
            continue
        m["latency"] += time.perf_counter() - t0
        m["invariant_violations"] += 0 if res.invariants.all_ok else 1
        # Score by RESULTING TEXT, not by exact edit strings (a correct edit may use a longer/shorter `find`).
        segs = {f"seg_{i + 1:04d}": t for i, t in enumerate(p["segments"])}
        from app.refinement.apply import render_refined
        auto_txt = {s.segment_id: s.text.lower() for s in res.refined_transcript.segments}
        both = render_refined(res.refined_transcript.model_copy(update={"segments": [s.model_copy(update={"text": segs[s.segment_id]}) for s in res.refined_transcript.segments]}),
                              [e.model_copy(update={"status": "applied"}) for e in res.edits if e.status != "rejected"])
        both_txt = {s.segment_id: s.text.lower() for s in both.segments}
        gold_spans = []  # (segment_id, start, end) of each gold `find` in the original text
        for gfind, grepl in p["gold"]:
            m["gold"] += 1
            seg_id = next(k for k, t in segs.items() if gfind in t)
            cs = segs[seg_id].index(gfind)
            gold_spans.append((seg_id, cs, cs + len(gfind)))
            if gfind.lower() not in auto_txt[seg_id] and grepl.lower() in auto_txt[seg_id]:
                m["fixed_auto"] += 1
            elif gfind.lower() not in both_txt[seg_id] and grepl.lower() in both_txt[seg_id]:
                m["fixed_suggested"] += 1
            else:
                m["missed"] += 1
        matched = set()
        for e in res.edits:
            m["edits"] += 1
            m["blocked"] += e.status == "rejected"
            m["verifier_vetoes"] += bool(e.verdict and (not e.verdict.same_meaning or e.verdict.new_information))
            near_gold = e.char_start is not None and any(e.segment_id == g[0] and e.char_start < g[2] and g[1] < e.char_end for g in gold_spans)
            if near_gold:
                matched.add(e.edit_id)
            if e.status == "applied":
                m["applied"] += 1
            if e.status == "rejected" or near_gold:
                continue
            m["false_applied" if e.status == "applied" else "false_suggested"] += 1
        if args.list:
            for e in res.edits:
                tag = "GOLD" if e.edit_id in matched else "other"
                print(f"  {p['id']:16} {e.status:9} {tag:5} {e.find!r} -> {e.replace!r}" + (f"  [{e.reject_reason}]" if e.reject_reason else ""))
    report.append({"refine_model": model, "effort": settings.refine_reasoning_effort, "verify_model": None if args.no_verify else settings.verify_model, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in m.items()}})
print(json.dumps(report, indent=2))

