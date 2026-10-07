"""Build a reference (ground truth) for a window of an AMI meeting from the public human annotations.

  python scripts/make_reference.py ES2002a 552.64 1272.64 samples/references/12_min.reference.json

Words are the manual, time-aligned transcript with speaker letters (A-D), shifted so 0 = start of the window. Meetings of the scenario series also
have a human summary (abstract, actions, decisions, problems) and timed decision annotations. AMI Manual Annotations release 1.7, CC BY 4.0.
"""
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZIP_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
ZIP_PATH = ROOT / "outputs" / "ami_public_manual.zip"  # outputs/ is not committed


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def get_zip() -> zipfile.ZipFile:
    if not ZIP_PATH.is_file():
        ZIP_PATH.parent.mkdir(exist_ok=True)
        print(f"downloading {ZIP_URL} (23 MB) ...", file=sys.stderr)
        urllib.request.urlretrieve(ZIP_URL, ZIP_PATH)
    return zipfile.ZipFile(ZIP_PATH)


TIMES: dict[str, tuple[float, float, str]] = {}  # every timed element id -> (start, end, speaker letter): annotations may point at punctuation or sounds


def read_words(z: zipfile.ZipFile, meeting: str) -> dict[str, list[dict]]:
    out = {}
    for sp in "ABCDE":
        name = f"words/{meeting}.{sp}.words.xml"
        if name not in z.namelist():
            continue
        words = []
        for el in ET.fromstring(z.read(name)):
            if el.get("starttime") is not None and el.get("endtime") is not None:
                TIMES[el.get("{http://nite.sourceforge.net/}id")] = (float(el.get("starttime")), float(el.get("endtime")), sp)
            if local(el.tag) != "w" or el.get("punc") == "true" or el.get("starttime") is None:
                continue
            words.append({"id": el.get("{http://nite.sourceforge.net/}id"), "w": (el.text or "").strip(), "start": float(el.get("starttime")), "end": float(el.get("endtime")), "spk": sp})
        out[sp] = words
    return out


def decisions(z: zipfile.ZipFile, meeting: str, by_speaker: dict[str, list[dict]]) -> list[dict]:
    name = f"decision/manual/{meeting}.decision.xml"
    if name not in z.namelist():
        return []
    out = []
    for d in ET.fromstring(z.read(name)):
        if local(d.tag) != "decision":
            continue
        words = []
        for c in d:
            href = c.get("href", "")
            if "#id(" not in href:
                continue
            ids = href.split("#id(", 1)[1].rstrip(")").split(")..id(")
            a, b = TIMES.get(ids[0]), TIMES.get(ids[-1])
            if a and b:
                words += [w for w in by_speaker[a[2]] if w["start"] >= a[0] - 1e-6 and w["end"] <= b[1] + 1e-6]
        if not words:
            continue
        words.sort(key=lambda w: w["start"])
        out.append({"start": words[0]["start"], "end": max(w["end"] for w in words), "text": " ".join(w["w"] for w in words)[:600]})
    return sorted(out, key=lambda x: x["start"])


def summary(z: zipfile.ZipFile, meeting: str) -> dict | None:
    name = f"abstractive/{meeting}.abssumm.xml"
    if name not in z.namelist():
        return None
    out = {}
    for el in ET.fromstring(z.read(name)):
        out.setdefault(local(el.tag), []).extend(" ".join((s.text or "").split()) for s in el if local(s.tag) == "sentence")
    return out


def main() -> None:
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    meeting, t0, t1, dest = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), Path(sys.argv[4])
    z = get_zip()
    by_speaker = read_words(z, meeting)
    if not by_speaker:
        sys.exit(f"no transcript for {meeting} in the AMI annotations")
    words = sorted((w for ws in by_speaker.values() for w in ws if t0 <= w["start"] < t1), key=lambda w: w["start"])
    shifted = [{"w": w["w"], "start": round(w["start"] - t0, 3), "end": round(w["end"] - t0, 3), "spk": w["spk"]} for w in words]
    dec = [{**d, "start": round(d["start"] - t0, 2), "end": round(d["end"] - t0, 2), "in_window": d["end"] > t0 and d["start"] < t1} for d in decisions(z, meeting, by_speaker)]
    ref = {"meeting": meeting, "window_seconds": [t0, t1], "source": "AMI Meeting Corpus manual annotations 1.7 (CC BY 4.0)",
           "speakers": sorted({w["spk"] for w in shifted}), "words": shifted, "decision_annotations": dec, "summary": summary(z, meeting)}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{dest}: {len(shifted)} words, speakers {ref['speakers']}, {sum(d['in_window'] for d in dec)} of {len(dec)} decision annotations in the window, summary: {'yes' if ref['summary'] else 'no'}")


if __name__ == "__main__":
    main()
