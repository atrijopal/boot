"""Score a finished job against a reference built by make_reference.py (human, public AMI annotations).

  python scripts/evaluate.py JOB_ID samples/references/12_min.reference.json [--refined]

--refined scores the transcript and speaker labels after phase 2 (e.g. labels the model inferred from the text for a provider with no voices).

Reports word error rate of the transcript, how often each word got the right speaker (best one-to-one matching of our labels to the real
speakers), and, when the reference has a summary and the job has minutes, whether the figures in the human decisions/actions appear in our records.
"""
import itertools
import json
import re
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOP = set("the a an of to will be is are was were and or in on for with at by from as that this it its their they he she we you i can cannot not no".split())
FILLERS = {"uh", "um", "mm", "hmm", "mhm", "mhmm", "erm", "er", "uhm", "umm", "mmm", "huh", "ah"}
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def num_words(n: int) -> list[str]:
    if n < 20:
        return [ONES[n]]
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [ONES[n // 100], "hundred"] + (num_words(n % 100) if n % 100 else [])
    return [ONES[n // 1000], "thousand"] + (num_words(n % 1000) if n % 1000 else [])


def tokens(text: str) -> list[str]:
    """Lowercase words; digits spelled out so '25' equals 'twenty five'; hyphens split; fillers dropped."""
    out = []
    for w in re.findall(r"[a-z0-9']+(?:\.[0-9]+)?", text.lower().replace("-", " ").replace("’", "'")):
        if re.fullmatch(r"\d+", w) and int(w) < 10000:
            out += num_words(int(w))
        elif w not in FILLERS:
            out.append(w)
    return out


def wer(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    """(WER, substitutions, deletions, insertions) by edit distance."""
    prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]  # (cost, sub, del, ins)
    for i in range(1, len(ref) + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, len(hyp) + 1):
            same = ref[i - 1] == hyp[j - 1]
            c, s, d, n = prev[j - 1]
            best = (c + (0 if same else 1), s + (0 if same else 1), d, n)
            c, s, d, n = prev[j]
            if c + 1 < best[0]:
                best = (c + 1, s, d + 1, n)
            c, s, d, n = cur[j - 1]
            if c + 1 < best[0]:
                best = (c + 1, s, d, n + 1)
            cur.append(best)
        prev = cur
    c, s, d, n = prev[-1]
    return c / max(1, len(ref)), s, d, n


def speaker_accuracy(ref_words: list[dict], hyp_words: list[dict]) -> tuple[float, dict, Counter, dict]:
    ref_words = [w for w in ref_words if tokens(w["w"])]
    starts = [w["start"] for w in ref_words]
    conf: Counter = Counter()
    for h in hyp_words:
        mid = (h["start"] + h["end"]) / 2
        i = bisect_right(starts, mid) - 1
        cands = [ref_words[k] for k in range(max(0, i - 1), min(len(ref_words), i + 2))]
        near = min(cands, key=lambda w: abs((w["start"] + w["end"]) / 2 - mid), default=None)
        if near and abs((near["start"] + near["end"]) / 2 - mid) <= 0.6:
            conf[(h["spk"], near["spk"])] += 1
    ours = sorted({k[0] for k in conf})
    real = sorted({k[1] for k in conf})
    best, best_map = -1, {}
    for perm in itertools.permutations(real + [None] * len(ours), len(ours)):
        score = sum(conf[(o, r)] for o, r in zip(ours, perm) if r)
        if score > best:
            best, best_map = score, {o: r for o, r in zip(ours, perm) if r}
    total = sum(conf.values())
    return best / max(1, total), best_map, conf, {"ours": ours, "real": real, "scored_words": total}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    job, ref = ROOT / "outputs" / args[0], json.loads(Path(args[1]).read_text(encoding="utf-8"))
    p1 = json.loads((job / "phase1.json").read_text(encoding="utf-8"))
    segs = p1["transcript"]["segments"]
    if "--refined" in sys.argv:  # score the speaker labels phase 2 worked out from the text
        segs = json.loads((job / "phase2.json").read_text(encoding="utf-8"))["refined"]["segments"]
    hyp_text = " ".join(s["text"] for s in segs)
    hyp, refw = tokens(hyp_text), tokens(" ".join(w["w"] for w in ref["words"]))
    rate, s, d, n = wer(refw, hyp)
    print(f"{p1['provider']} / {p1['asr_model']}  |  {ref['meeting']} window {ref['window_seconds']}  |  {len(refw)} reference words, {len(hyp)} transcribed")
    print(f"WORD ERROR RATE {rate:.1%}  (substitutions {s}, deletions {d}, insertions {n}; fillers removed, digits spelled out)")
    words = [{"start": w["start"], "end": w["end"], "spk": sg["speaker"] if "--refined" in sys.argv else (w.get("speaker") or sg["speaker"])} for sg in segs if sg["speaker_known"] for w in sg["words"]]
    if words:
        acc, mapping, conf, info = speaker_accuracy(ref["words"], words)
        print(f"SPEAKERS  we found {len(info['ours'])}, the meeting has {len(ref['speakers'])}; {acc:.1%} of {info['scored_words']} words got the right speaker")
        print("  our label -> real speaker:", {k: v for k, v in mapping.items()})
        print("  confusion (our label: real speaker -> words):")
        for o in info["ours"]:
            print(f"    {o}: " + ", ".join(f"{r}={conf[(o, r)]}" for r in info["real"]))
    else:
        print("SPEAKERS  this transcript has no speaker labels (the provider cannot tell voices apart)")
    res = job / "result.json"
    if ref.get("summary") and res.is_file():
        r = json.loads(res.read_text(encoding="utf-8"))
        print("\nHUMAN SUMMARY vs OUR RECORDS (a human sentence counts as FOUND if some decision, action or proposal of ours holds at least half of its content words)")
        pool = [(x["kind"], x["text"], set(tokens(x["text"] + " " + " ".join(e["quote"] for e in x["evidence"])))) for x in r["records"] if x["kind"] in ("decision", "action", "proposal")]
        found_by_kind: Counter = Counter()
        for kind in ("decisions", "actions", "problems"):
            for sent in ref["summary"].get(kind, []):
                want = {w for w in tokens(sent) if w not in STOP}
                best = max(((len(want & ws) / max(1, len(want)), k, t) for k, t, ws in pool), default=(0.0, "", ""))
                hit = best[0] >= 0.5
                found_by_kind[kind] += hit
                print(f"  [{kind[:-1]:8}] {'FOUND  ' if hit else 'missing'} {sent[:78]}" + (f"   <- {best[1]}: {best[2][:70]} ({best[0]:.0%})" if best[0] > 0 else ""))
        n = {k: len(ref["summary"].get(k, [])) for k in ("decisions", "actions", "problems")}
        print(f"  RECALL  decisions {found_by_kind['decisions']}/{n['decisions']}, actions {found_by_kind['actions']}/{n['actions']}  |  we recorded {sum(1 for x in r['records'] if x['kind'] == 'decision')} decisions and {sum(1 for x in r['records'] if x['kind'] == 'action')} action items, {len(r['records'])} records in all")


if __name__ == "__main__":
    main()
