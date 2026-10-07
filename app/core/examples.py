"""Saved example meetings: real records this application produced earlier, kept in the repository so they open instantly without any processing.

They are copied into the outputs folder at start-up if missing. They are labelled as examples everywhere they are shown, and they can not be re-run (that would
spend model calls and would not be an upload of the user's own recording). A new upload always goes through the real pipeline.
"""
import shutil
from pathlib import Path

from app.config.settings import ROOT, settings

SOURCE = ROOT / "samples" / "example_meetings"


def seed() -> list[str]:
    out = []
    for d in sorted(SOURCE.iterdir()) if SOURCE.is_dir() else []:
        dest = Path(settings.outputs_dir) / d.name
        if d.is_dir() and not (dest / "session.json").is_file():
            shutil.copytree(d, dest, dirs_exist_ok=True)
            out.append(d.name)
    return out
