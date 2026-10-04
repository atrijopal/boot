"""Logging setup. Stage timings and errors only; transcript text is never logged."""
import logging
import time
from contextlib import contextmanager
from pathlib import Path

_configured = False


def get_logger(name: str = "phase1") -> logging.Logger:
    global _configured
    if not _configured:
        from app.config.settings import settings

        log_dir = Path(settings.outputs_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        root = logging.getLogger("phase1")
        root.setLevel(logging.INFO)
        for h in (logging.StreamHandler(), logging.FileHandler(log_dir / "phase1.log")):
            h.setFormatter(fmt)
            root.addHandler(h)
        _configured = True
    return logging.getLogger(name)


@contextmanager
def timed(timings: dict, stage: str, log: logging.Logger | None = None):
    """Record wall-clock seconds for a stage into `timings[stage]`."""
    log = log or get_logger()
    log.info("stage_start %s", stage)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[stage] = round(time.perf_counter() - t0, 3)
        log.info("stage_end %s %.3fs", stage, timings[stage])
