"""Usage: python scripts/run_phase3.py JOB_ID (after Phase 2)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.summarization.pipeline import run_phase3
from app.utils.errors import PipelineError


def main():
    parser = argparse.ArgumentParser(description="Generate evidence-backed minutes for a saved Phase 2 job")
    parser.add_argument("job_id")
    args = parser.parse_args()
    try:
        result = run_phase3(args.job_id, lambda s, m: print(f"{s}: {m}", flush=True))
    except PipelineError as err:
        print(f"{err.code}: {err.message}", file=sys.stderr)
        return 1
    print(f"Status: {result.manifest.status}; run: {result.manifest.run_id}")
    print(f"Download through /jobs/{args.job_id}/phase3/download/minutes")
    return 0 if result.manifest.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

