"""Second-model check (larger than the refiner) that an edit only repairs a recognition error."""
import hashlib
import json

from app.config.settings import ROOT, settings
from app.llm.client import chat_json
from app.schemas.refinement import Edit, Verdict
from app.utils import cache
from app.utils.errors import PipelineError
from app.utils.logging import get_logger

log = get_logger("verify")
PROMPT_FILE = ROOT / "prompts" / "verify_v1.md"
BATCH = 15


def verify_edits(items: list[tuple[Edit, str, str]]) -> tuple[dict[str, Verdict], list[str]]:
    """items = [(edit, segment_text_before, segment_text_after)]. Missing verdicts mean the verifier was unavailable."""
    system = PROMPT_FILE.read_text(encoding="utf-8")
    verdicts: dict[str, Verdict] = {}
    warnings: list[str] = []
    for i in range(0, len(items), BATCH):
        chunk = items[i : i + BATCH]
        payload = [{"edit_id": e.edit_id, "before": b, "after": a, "changed_from": e.find, "changed_to": e.replace} for e, b, a in chunk]
        user = "EDITS TO CHECK (data):\n<data>\n" + "\n".join(json.dumps(p, ensure_ascii=False) for p in payload) + "\n</data>"
        key = "verify_" + hashlib.sha256(f"{settings.verify_model}|{system}|{user}".encode()).hexdigest()[:32]
        data = cache.load_key(key)
        try:
            if data is None:
                data = chat_json(system, user, model=settings.verify_model)
                cache.store_key(key, data)
        except PipelineError as e:
            warnings.append(f"Verification unavailable ({e.message}); affected edits are marked unverified.")
            continue
        ids = {e.edit_id for e, _, _ in chunk}
        for v in data.get("verdicts", []) if isinstance(data, dict) else []:
            if isinstance(v, dict) and v.get("edit_id") in ids and isinstance(v.get("same_meaning"), bool):
                verdicts[v["edit_id"]] = Verdict(
                    same_meaning=v["same_meaning"],
                    new_information=v.get("new_information") if isinstance(v.get("new_information"), bool) else None,
                    note=str(v.get("note", ""))[:200],
                )
    return verdicts, warnings

