"""Count-only diagnostic for DENS_CAPS (ADR 0010). Read-only; prints NUMBERS ONLY, never a word.

    python stripper/capsdiag.py --drop D --state S --directory P [--vocab F] [--ignore F]

--state must be an existing baseline state folder (key store present); the tool never creates or changes it, and writes
no file of any kind. Every label printed is a fixed string and every value is a count, so the output is safe to keep and
to paste. It evaluates each unique message through the production path at the default limit of 1 and reports:
outcome groups, unknown-cap histogram, release curve by limit (per field as in production, and per message total),
section split, shape split, overlap with other word sets, distinct words, frequency of frequency, and what a vocabulary
would buy (coverage by the top N words ranked by thread count).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capsscan as S  # noqa: E402
import ner as N  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402

VERSION = T.STRIPPER_VERSION
BINS = ((0, "0"), (1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5-9"), (10, "10-19"), (20, "20+"))
FREQ_BINS = ((1, "1"), (2, "2"), (3, "3-4"), (5, "5-9"), (10, "10+"))
CURVE = tuple(range(1, 11))
TOP_N = (10, 25, 50, 100, 200)


def _bin(n: int, bins) -> str:
    label = bins[0][1]
    for lo, lab in bins:
        if n >= lo:
            label = lab
    return label


def _order(bins) -> list:
    return [lab for _, lab in bins]


def group_of(m: S.Scanned) -> str:
    if not m.reasons:
        return "released today"
    if set(m.reasons) == {"DENS_CAPS"}:
        return "DENS_CAPS only"
    if "DENS_CAPS" in m.reasons:
        return "DENS_CAPS plus other reasons"
    return "other reasons only"


GROUPS = ("released today", "DENS_CAPS only", "DENS_CAPS plus other reasons", "other reasons only")


def build_report(res: S.ScanResult) -> list[str]:
    L: list[str] = []
    add = L.append
    ms = res.msgs
    tx = [m for m in ms if m.transformed]
    add(f"nwgms-stripper {VERSION} caps diagnostic (counts only, no words)")
    add(f"files read: {res.files}")
    add(f"unreadable: {res.unreadable}")
    add(f"schema rejects: {res.schema_rejects}")
    add(f"front-end errors: {res.front_errors}")
    add(f"duplicates skipped: {res.duplicates}")
    add(f"messages evaluated: {len(ms)}")
    add(f"not transformed (size or error): {len(ms) - len(tx)}")

    add("")
    add("== outcome today, limit 1 ==")
    g = Counter(group_of(m) for m in ms)
    for name in GROUPS:
        add(f"{name}: {g[name]}")

    add("")
    add("== unknown capitalised words per message (histogram, total across fields) ==")
    for name in GROUPS[1:3]:
        h = Counter(_bin(m.total, BINS) for m in tx if group_of(m) == name)
        add(f"{name}: " + ", ".join(f"{lab} {h[lab]}" for lab in _order(BINS)))

    add("")
    add("== release curve: messages that would release at each limit (no other reason) ==")
    clean = [m for m in tx if not m.others]
    add("limit, per field (production above 1 uses message total: ADR 0010), message total")
    for k in CURVE:
        pf = sum(1 for m in clean if (max(m.field_totals) if m.field_totals else 0) < k)
        tt = sum(1 for m in clean if m.total < k)
        add(f"limit {k}: {pf}, {tt}")

    add("")
    add("== sections (occurrences, messages with at least one) ==")
    for sec in S.SECTIONS:
        occ = sum(m.profiles[sec]["total"] for m in tx)
        n = sum(1 for m in tx if m.profiles[sec]["total"])
        add(f"{sec}: {occ}, {n}")
    capsm = [m for m in tx if m.total]
    sub = sum(1 for m in capsm if m.profiles["subject"]["total"] and not m.profiles["body"]["total"]
              and not any(m.profiles[h]["total"] for h in ("from", "to", "cc")))
    body = sum(1 for m in capsm if m.profiles["body"]["total"] and not m.profiles["subject"]["total"]
               and not any(m.profiles[h]["total"] for h in ("from", "to", "cc")))
    both = sum(1 for m in capsm if m.profiles["subject"]["total"] and m.profiles["body"]["total"])
    hdr = sum(1 for m in capsm if any(m.profiles[h]["total"] for h in ("from", "to", "cc")))
    add(f"messages with caps only in subject: {sub}")
    add(f"messages with caps only in body: {body}")
    add(f"messages with caps in subject and body: {both}")
    add(f"messages with caps in a header field: {hdr}")

    add("")
    add("== shape of each occurrence (occurrences, messages with at least one) ==")
    for key in R.PROFILE_KEYS:
        occ = sum(m.profiles[s][key] for m in tx for s in S.SECTIONS)
        n = sum(1 for m in tx if any(m.profiles[s][key] for s in S.SECTIONS))
        add(f"{key}: {occ}, {n}")

    stats = S.word_stats(res)
    add("")
    add("== overlap with other word sets (distinct words, occurrences) ==")
    sets = {"directory token": lambda w: any(p in res.directory_tokens for p in w.key.split("-")),
            "vocab file word": lambda w: w.key in res.vocab,
            "external header participant word": lambda w: w.ext,
            "after a patient cue": lambda w: w.cue,
            "also seen in lower case in the drop": lambda w: w.lower}
    for label, fn in sets.items():
        ws = [w for w in stats.values() if fn(w)]
        add(f"{label}: {len(ws)}, {sum(w.occ for w in ws)}")

    add("")
    add("== distinct words ==")
    add(f"distinct unknown words: {len(stats)}")
    add(f"occurrences: {sum(w.occ for w in stats.values())}")
    for sec in S.SECTIONS:
        add(f"distinct in {sec}: {sum(1 for w in stats.values() if sec in w.sections)}")
    add("words by number of messages containing them: " + ", ".join(
        f"{lab} {sum(1 for w in stats.values() if _bin(w.msgs, FREQ_BINS) == lab)}" for lab in _order(FREQ_BINS)))
    add("words by number of threads containing them: " + ", ".join(
        f"{lab} {sum(1 for w in stats.values() if _bin(len(w.threads), FREQ_BINS) == lab)}" for lab in _order(FREQ_BINS)))
    add("distinct threads: " + str(len({m.thread for m in ms})))

    add("")
    add("== what a vocabulary would buy (DENS_CAPS-only messages that would clear; approximate) ==")
    co = S.caps_only(res)
    add(f"DENS_CAPS-only messages: {len(co)}")
    ranked = sorted(stats.values(), key=lambda w: (-len(w.threads), -w.msgs, w.key))
    for n in TOP_N:
        add(f"top {n} words by threads: {S.would_clear(co, {w.key for w in ranked[:n]})}")
    add(f"all {len(ranked)} words: {S.would_clear(co, {w.key for w in ranked})}")
    low = {w.key for w in ranked if w.lower}
    add(f"words also seen in lower case ({len(low)}): {S.would_clear(co, low)}")
    return L


def main(argv=None, protector=None, use_ner: bool = True, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drop", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--directory", required=True, type=Path)
    ap.add_argument("--vocab", type=Path)
    ap.add_argument("--ignore", type=Path)
    a = ap.parse_args(argv)
    try:
        S.check_read_paths(a.drop, a.state, a.vocab, a.ignore)
        vocab = R.load_wordlist(a.vocab) if a.vocab else frozenset()
        ignore = R.load_wordlist(a.ignore) if a.ignore else frozenset()
        directory = a.directory.read_text(encoding="utf-8")
        if use_ner:
            N.load()
            if not str(N.STATUS).startswith("active"):
                raise S.Refused("the NER layer is not active; real runs require it")
        res = S.scan_drop(a.drop, a.state, directory, vocab, ignore, use_ner=use_ner, protector=protector,
                          vocab_path=a.vocab, ignore_path=a.ignore)
    except S.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never print an exception message: it could carry content
        print(f"error: {type(exc).__name__} (message suppressed)", file=sys.stderr)
        return 1
    print("\n".join(build_report(res)), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
