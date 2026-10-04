"""CLI: python scripts/run_phase1.py AUDIO [--provider groq|deepgram]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preprocessing.pipeline import run_phase1  # noqa: E402
from app.utils.errors import PipelineError  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--provider", default=None)
args = ap.parse_args()
try:
    r = run_phase1(args.audio, provider_name=args.provider, status=lambda s, m: print(f"[{s}] {m}", file=sys.stderr))
except PipelineError as e:
    sys.exit(f"ERROR {e.code}: {e.message}")
f = r.protected_facts
print(json.dumps({
    "job_id": r.job_id, "provider": r.provider, "model": r.model, "segments": len(r.raw_transcript.segments),
    "numbers": [n.surface for n in f.numbers], "people": [p.value for p in f.people], "terms": r.meeting_context.terms[:15],
    "timings_seconds": r.timings_seconds, "output_dir": f"outputs/{r.job_id}",
}, indent=2))
