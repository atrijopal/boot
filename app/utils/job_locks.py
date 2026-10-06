"""Process-local locks shared by Phase 2 writes and Phase 3 snapshot publication.

The demo server runs in one process. Multiple workers need an external lock/store.
"""
from functools import wraps
from threading import Lock, RLock

_guard = Lock()
_locks: dict[str, RLock] = {}


def job_lock(job_id: str):
    with _guard:
        return _locks.setdefault(job_id, RLock())


def locked_job(fn):
    @wraps(fn)
    def wrapped(job_id, *args, **kwargs):
        with job_lock(job_id):
            return fn(job_id, *args, **kwargs)
    return wrapped

