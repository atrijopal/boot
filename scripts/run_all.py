"""Run the whole pipeline on one audio file from the terminal.

python scripts/run_all.py AUDIO [--provider groq|deepgram] [--no-minutes]
Phase 1 (transcript + facts) -> Phase 2 (refined transcript + speakers) -> Phase 3 (minutes). Results go to outputs/<job_id>/.
Open the same job in the web page afterwards with: http://localhost:8000/?job=<job_id>
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings  # noqa: E402
from app.minutes.pipeline import run_minutes  # noqa: E402
from app.preprocessing.pipeline import run_phase1  # noqa: E402
from app.refinement.pipeline import run_refinement  # noqa: E402
from app.utils.errors import PipelineError  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--provider", default=None, help="groq (default) or deepgram")
ap.add_argument("--no-minutes", action="store_true", help="stop after Phase 2")
args = ap.parse_args()

t0 = time.time()
say = lambda tag: (lambda stage, msg="": print(f"[{time.time() - t0:6.1f}s] {tag} {stage}{': ' + msg if msg and not stage.endswith(':done') else ''}", file=sys.stderr, flush=True))
try:
    r1 = run_phase1(args.audio, provider_name=args.provider, status=say("phase 1"))
    print(f"Phase 1 done: job {r1.job_id}, {len(r1.normalized_transcript.segments)} segments")
    r2 = run_refinement(r1.job_id, status=say("phase 2"))
    print(f"Phase 2 done: {r2.stats['applied']} edits applied, {r2.stats['suggested']} suggested, {r2.stats['rejected']} blocked, {r2.stats.get('speakers_named', 0)} speakers named")
    if not args.no_minutes:
        m = run_minutes(r1.job_id, status=say("phase 3"))
        counts = ", ".join(f"{k}: {v}" for k, v in m.stats.items() if k in ("decision", "action", "question", "proposal", "disagreement", "risk", "fact", "needs_review"))
        print(f"Phase 3 done ({m.status}): {counts or 'no verified items'}")
        for w in m.warnings[:5]:
            print(f"  note: {w[:200]}")
except PipelineError as e:
    sys.exit(f"ERROR {e.code}: {e.message}")
print(f"\nOutputs: outputs/{r1.job_id}/   (minutes: outputs/{r1.job_id}/minutes/runs/*/minutes.md)")
print(f"Web page: http://localhost:8000/?job={r1.job_id}   (start it with: bash run.sh)")
