"""Versioned JSON calls through the existing client and cache."""
import hashlib
import json

from app.config.settings import ROOT, settings
from app.llm.client import chat_json
from app.utils import cache
from app.utils.errors import PipelineError

PROMPTS = ("events", "verify", "relations", "summary")


def prompt_hashes():
    return {name: hashlib.sha256((ROOT / "prompts" / f"phase3_{name}_v1.md").read_bytes()).hexdigest() for name in PROMPTS}


def ask(stage, data, model, run_id):
    system = (ROOT / "prompts" / f"phase3_{stage}_v1.md").read_text(encoding="utf-8")
    user = json.dumps(data, ensure_ascii=False, sort_keys=True)
    identity = "|".join([run_id, settings.llm_base_url, model, system, user])
    key = "phase3_" + hashlib.sha256(identity.encode()).hexdigest()
    value = cache.load_key(key)
    if value is None:
        value = chat_json(system, user, model=model)
        if not isinstance(value, dict):
            raise PipelineError("PHASE3_BAD_RESPONSE", "The Phase 3 model did not return a JSON object.")
        key_name = {"events": "events", "relations": "relations", "verify": "verdicts", "summary": "claims"}[stage]
        if not isinstance(value.get(key_name), list):
            raise PipelineError("PHASE3_BAD_RESPONSE", "The Phase 3 model omitted its required list.")
        if stage == "verify":
            ids = [row.get("id") for row in value["verdicts"] if isinstance(row, dict)]
            expected = [row["id"] for row in data["records"]]
            if len(ids) != len(set(ids)) or set(ids) != set(expected):
                raise PipelineError("PHASE3_BAD_RESPONSE", "The Phase 3 verifier omitted or duplicated a verdict.")
        cache.store_key(key, value)
    return value


def pack(items, max_chars, overhead=1000):
    """Bound serialized input size. Oversized individual records fail explicitly."""
    batch, size = [], overhead
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False)) + 2
        if length + overhead > max_chars:
            raise PipelineError("PHASE3_RECORD_TOO_LARGE", "A Phase 3 evidence record exceeds the configured batch limit.")
        if batch and size + length > max_chars:
            yield batch
            batch, size = [], overhead
        batch.append(item)
        size += length
    if batch:
        yield batch

