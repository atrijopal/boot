"""Code-found hints for the refinement step: a phrase said again nearby and written differently ("canal number ... Channel numbers").

The second time is often the recogniser getting it right. This costs no tokens; the language model and the gates decide whether it is a mis-hearing."""
import difflib
import re

from wordfreq import zipf_frequency

_TOK = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")

SMALL = set("""the a an of to is are was were be been and or in on for with at by from as that this it its it's we you i he she they me my our your their re s t ll ve d m am
yeah yes no okay ok uh um mm hmm mhm mmhmm huh oh so but well just like then there here not do does did have has had gonna wanna going would will shall can could should might may to got get""".split())


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _us(s: str) -> str:
    """A rough US/UK spelling fold, so colour/color or grey/gray are not reported as mis-hearings."""
    k = _key(s)
    for a, b in (("ou", "o"), ("grey", "gray"), ("ise", "ize"), ("yse", "yze"), ("tre", "ter"), ("ll", "l")):
        k = k.replace(a, b)
    return k


def find(units, limit: int = 8) -> list[tuple[str, str, str]]:
    """(unit id, earlier wording, later wording), the likeliest mis-hearings first."""
    rows = []
    for u in units:
        toks = [(m.group().lower(), m.start(), m.end()) for m in _TOK.finditer(u.text)]
        for n in (3, 2, 1):
            for x in range(len(toks) - n + 1):
                a = toks[x : x + n]
                pa = " ".join(w[0] for w in a)
                if all(w[0] in SMALL for w in a) or len(_key(pa)) < 5 or any(ch.isdigit() for ch in pa):
                    continue
                for m in (n, n + 1):
                    for y in range(x + n, min(len(toks) - m + 1, x + n + 14)):
                        b = toks[y : y + m]
                        pb = " ".join(w[0] for w in b)
                        ka, kb = _key(pa), _key(pb)
                        if ka == kb or _us(pa) == _us(pb) or ka.startswith(kb) or kb.startswith(ka) or all(w[0] in SMALL for w in b):
                            continue
                        ta, tb = [w[0] for w in a], [w[0] for w in b]
                        da, db = [w for w in ta if w not in SMALL and w not in tb], [w for w in tb if w not in SMALL and w not in ta]
                        if not da or not db or all(any(x.startswith(y) or y.startswith(x) for y in db) for x in da):
                            continue  # nothing but function words or plural/tense endings differ
                        if difflib.SequenceMatcher(None, _key("".join(da)), _key("".join(db))).ratio() < 0.62:
                            continue  # the words that differ must sound alike ("canal" / "channel"), not just fill the same slot ("keep" / "make")
                        if min(zipf_frequency(w, "en") for w in da + db) >= 4.5:
                            continue  # two everyday words ("think" / "thing") are the speaker's own choice far more often than a mis-hearing
                        r = difflib.SequenceMatcher(None, ka, kb).ratio()
                        if r >= 0.7 and not re.search(r"[.!?]\s", u.text[a[0][1] : a[-1][2]]) and not re.search(r"[.!?]\s", u.text[b[0][1] : b[-1][2]]):
                            rows.append((r + 0.05 * n, u.uid, u.text[a[0][1] : a[-1][2]], u.text[b[0][1] : b[-1][2]]))
    rows.sort(key=lambda r: -r[0])
    seen, out = set(), []
    for _, uid, first, later in rows:
        if first.lower() not in seen:
            seen.add(first.lower())
            out.append((uid, first, later))
        if len(out) >= limit:
            break
    return out
