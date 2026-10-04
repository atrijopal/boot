"""Download a short public-domain English speech clip for smoke-testing (NOT a meeting)."""
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac"  # JFK inaugural excerpt, US public domain
dest = Path(__file__).resolve().parents[1] / "samples" / "jfk.flac"
dest.parent.mkdir(exist_ok=True)
urllib.request.urlretrieve(URL, dest)
print(f"saved {dest} ({dest.stat().st_size} bytes)", file=sys.stderr)
