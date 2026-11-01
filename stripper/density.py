"""Density-check sweep over the synthetic corpus (ADR 0005). Counts only, throwaway stores.

For each unknown-capitalised threshold it reports how many CLEAN messages (no planted name fragment
surviving the transform) would be quarantined, and how many LEAKY messages (a planted name fragment
survived) would be caught, split by miss kind. Run with --ner for the number that matters.

    python stripper/density.py [--n 1200] [--seed 20261027] [--ner]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "corpus"))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import pipeline as P  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402


def main() -> None:
    import generate as C
    ap = argparse.ArgumentParser(description="Density threshold sweep (counts only)")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--ner", action="store_true")
    ap.add_argument("--words", action="store_true", help="also list the most common words behind the counts (synthetic vocabulary only)")
    a = ap.parse_args()
    if a.ner:
        N.load()
    msgs = C.generate(a.n, a.seed)
    vocab = P.corpus_vocab()
    with tempfile.TemporaryDirectory() as td:
        st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
        tr = T.Transformer(st, G.parse_directory(C.directory_markdown()), use_ner=a.ner, year=2026)
        rows = []
        words, places = Counter(), Counter()
        for m in msgs:
            hdr = m["meta"].get("header_people", [])
            r = tr.message(m["fields"], hdr, commit=False, audit=False)
            body = "\n".join(v for k, v in r.fields.items() if not k.startswith("attachment_"))
            allowed = R.allowed_words(tr.people.with_header(hdr), tr.ignore, vocab, hdr)
            frag = P.name_fragments_surviving(m, r.fields)
            if a.words and not frag:
                words.update(R.unknown_cap_words(body, allowed))
            if a.words and a.ner:
                blank = T.blank_approved(body)
                places.update(blank[h.start:h.end] for h in N.detect_ner(blank) if h.code == "NER_LOCATION")
            rows.append((R.unknown_caps(body, allowed), P.name_fragments_surviving(m, r.fields), body.count("[CLIN]")))
    leaky = [r for r in rows if r[1]]
    clean = [r for r in rows if not r[1]]
    print(f"messages={len(rows)} NER: {N.STATUS if a.ner else 'off'}  clean={len(clean)} leaky(name fragment survives transform)={len(leaky)}")
    print("unknown-capitalised count, clean:", sorted(Counter(r[0] for r in clean).items()))
    print("unknown-capitalised count, leaky:", sorted(Counter(r[0] for r in leaky).items()))
    kinds = Counter(k for r in leaky for k in r[1])
    for t in (1, 2, 3):
        qc = sum(1 for r in clean if r[0] >= t)
        caught = Counter(k for r in leaky if r[0] >= t for k in r[1])
        miss = Counter(k for r in leaky if r[0] < t for k in r[1])
        print(f"caps>={t}: clean quarantined {qc}/{len(clean)} ({100 * qc / max(1, len(clean)):.1f}%); "
              f"leaky caught {sum(1 for r in leaky if r[0] >= t)}/{len(leaky)}; uncaught by kind {dict(miss)}")
    if a.words:
        print("most common unknown capitalised words in CLEAN messages:", words.most_common(25))
        if a.ner:
            print("most common NER_LOCATION hits in transformed output:", places.most_common(15))
    print("[CLIN] markers per message:", sorted(Counter(r[2] for r in rows).items()))
    for t in (2, 3, 4):
        print(f"clin>={t}: {sum(1 for r in rows if r[2] >= t)} messages ({100 * sum(1 for r in rows if r[2] >= t) / len(rows):.1f}%)")


if __name__ == "__main__":
    main()
