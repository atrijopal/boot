"""Measure ASR latency / real-time factor per provider. Only measured numbers are printed.

python scripts/benchmark_asr.py AUDIO --provider groq [--provider deepgram] [--no-cache]
Note: API latency includes network time and provider-side queueing.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.asr.registry import get_provider  # noqa: E402
from app.audio.prepare import prepare_audio  # noqa: E402
from app.audio.validate import validate_file  # noqa: E402
from app.preprocessing.normalize import normalize_transcript  # noqa: E402
from app.preprocessing.pipeline import extract_facts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--provider", action="append", default=None)
args = ap.parse_args()

p = validate_file(args.audio)
prepared = prepare_audio(p)
dur = prepared.metadata.duration_seconds
rows = []
for name in args.provider or ["groq"]:
    prov = get_provider(name)
    t0 = time.perf_counter()
    res = prov.transcribe(p, prepared.samples, prepared.sample_rate)
    asr_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    norm = normalize_transcript(res.transcript)
    extract_facts(norm)
    ext_s = time.perf_counter() - t1
    rows.append({
        "provider": prov.name, "model": res.transcript.model, "audio_seconds": dur, "asr_seconds": round(asr_s, 2),
        "real_time_factor": round(asr_s / dur, 4), "extraction_seconds": round(ext_s, 2), "chunks": res.chunks,
        "segments": len(res.transcript.segments), "words": sum(len(s.text.split()) for s in res.transcript.segments),
    })
print(json.dumps(rows, indent=2))
