"""Evaluate a saved run against explicit labels; never treat fixtures as real accuracy."""
import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.phase3 import Phase3Result


def evaluate(result, labels):
    units = {u.unit_id: u for u in result.units}
    pairs = []
    gold = labels.get("topic_labels", {})
    for a, b in combinations(gold, 2):
        ua, ub = units.get(a), units.get(b)
        if ua is None or ub is None:
            raise ValueError("Label references a unit missing from this run")
        pa = max(ua.memberships, key=ua.memberships.get) if ua.memberships else None
        pb = max(ub.memberships, key=ub.memberships.get) if ub.memberships else None
        predicted = pa is not None and pb is not None and pa == pb
        pairs.append((predicted, gold[a] == gold[b]))
    tp = sum(p and g for p, g in pairs)
    fp = sum(p and not g for p, g in pairs)
    fn = sum(not p and g for p, g in pairs)
    required = labels.get("required_events", [])
    supported = [e.model_dump() for e in result.events if e.validation_status == "supported"]
    matched = [any(all(row.get(k) == v for k, v in expected.items()) for row in supported) for expected in required]
    return {
        "dataset_note": labels.get("dataset_note", "User-provided labels"),
        "source_revision": result.manifest.source_revision,
        "run_status": result.manifest.status,
        "topic_pairs": len(pairs), "topic_pair_precision": tp/(tp+fp) if tp+fp else None,
        "topic_pair_recall": tp/(tp+fn) if tp+fn else None,
        "required_event_matches": sum(matched), "required_events": len(required),
        "missing_required_events": [e for e, ok in zip(required, matched) if not ok],
        "structural_checks": result.validation_report,
        "warning": "These labels do not measure unannotated hallucinations or general real-meeting accuracy.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="phase3_result.json path")
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = Phase3Result.model_validate_json(args.result.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    report = evaluate(result, labels)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

