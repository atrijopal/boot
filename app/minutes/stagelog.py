"""Records every LLM call (input and raw output) and every keep/drop decision our code makes on it, so the UI can show
the whole pipeline step by step and a reviewer can see exactly why something is, or is not, in the final report."""
import threading


class StageLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.outcomes: dict[str, list[dict]] = {}
        self.meta: dict = {}

    def call(self, rec: dict) -> int:
        with self.lock:
            rec["call_id"] = len(self.calls) + 1
            self.calls.append(rec)
            return rec["call_id"]

    def outcome(self, stage: str, **rec) -> None:
        with self.lock:
            self.outcomes.setdefault(stage, []).append(rec)

    def export(self) -> dict:
        with self.lock:
            return {
                "meta": self.meta,
                "calls": sorted(self.calls, key=lambda c: (c.get("order", 0), c["call_id"])),
                "outcomes": {k: list(v) for k, v in self.outcomes.items()},
            }
