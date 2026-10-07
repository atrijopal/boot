"""Run the whole pipeline on one audio file: python scripts/run_all.py AUDIO [--provider groq|deepgram]"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.pipeline import run_pipeline  # noqa: E402
from app.utils.errors import PipelineError  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--provider", default=None, help="groq (default) or deepgram (adds speaker labels)")
args = ap.parse_args()
t0 = time.time()
try:
    r = run_pipeline(args.audio, provider_name=args.provider, status=lambda s, m="": print(f"[{time.time() - t0:5.1f}s] {s}: {m}", file=sys.stderr, flush=True))
except PipelineError as e:
    sys.exit(f"ERROR {e.code}: {e.message}")
u = r.usage
from collections import Counter  # noqa: E402

print(f"\nDone ({r.status}) in {time.time() - t0:.0f}s. Model use: {u.calls} call(s), {u.total_tokens} tokens ({u.prompt_tokens} in / {u.completion_tokens} out).")
print("Records:", dict(Counter(x.kind for x in r.records)), "| edits:", dict(Counter(e.status for e in r.edits)), "| speakers named:", len(r.speaker_names))
for w in r.warnings[:5]:
    print("  note:", w[:200])
print(f"\nOutputs: outputs/{r.job_id}/   (report.html, minutes.md, minutes.json, action_items.csv, transcript.txt, calls.json)")
