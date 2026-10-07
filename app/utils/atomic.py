"""Write a file so that a reader never sees it half written: write beside it, then rename over it."""
import os
from pathlib import Path


def atomic_write(path: str | Path, text: str) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
