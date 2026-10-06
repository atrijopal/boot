"""CLI: python scripts/run_phase2.py JOB_ID (after Phase 1)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.refinement.pipeline import job_dir, run_refinement
from app.utils.errors import PipelineError


def main():
    parser = argparse.ArgumentParser(description="Refine a saved Phase 1 transcript")
    parser.add_argument("job_id")
    args = parser.parse_args()
    try:
        result = run_refinement(args.job_id, lambda stage, message: print(f"{stage}: {message}", flush=True))
    except PipelineError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    print(f"Saved: {job_dir(args.job_id) / 'refinement.json'}")
    print(f"Invariant checks passed: {result.invariants.all_ok}")
    return 0 if result.invariants.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
