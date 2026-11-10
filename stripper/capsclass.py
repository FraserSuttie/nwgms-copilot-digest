"""Count-only measure of the draft caps classifier (draft ADR 0017). Read-only; prints NUMBERS ONLY, never a word.

    python stripper/capsclass.py --drop D --state S --directory P [--vocab F] [--ignore F] [--phrases F]

--state must be an existing baseline state folder (key store present); nothing is created, changed or written. Each
unknown capitalised word in a message held for DENS_CAPS only is scored on three checks (residual.classify_caps):
position (subject or Title Case line is neutral), shape (title, possessive, pair, alone on a line, after a greeting,
header field, NER person) and nearby identifiers. Then it reports what two draft policies would release:
  A: release when every unknown word is FLAG (neutral position, no name shape, no identifier nearby)
  B: release when no unknown word is HOLD (no name shape, no identifier nearby; ordinary prose words allowed)
Nothing in the stripper changes; this only measures.
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


def build_report(res: S.ScanResult) -> list[str]:
    msgs = [m for m in res.msgs]
    released = sum(1 for m in msgs if m.transformed and not m.reasons)
    held = len(msgs) - released
    co = S.caps_only(res)
    pols = [m.policy for m in co if m.policy is not None]
    a = sum(1 for p in pols if p["release_a"])
    b = sum(1 for p in pols if p["release_b"])
    tiers, signals, hold_sig, msg_hold = Counter(), Counter(), Counter(), Counter()
    for p in pols:
        tiers.update(p["tiers"])
        signals.update(p["signals"])
        hold_sig.update(p["hold_signals"])
        if not p["release_b"]:
            msg_hold.update(p["hold_signals"].keys())
    n = len(msgs) or 1
    out = [f"nwgms-stripper {VERSION} caps classifier (draft ADR 0017; counts only; NER: {N.STATUS})",
           f"messages: {len(msgs)}  released today: {released}  held today: {held}  held for DENS_CAPS only: {len(co)}",
           f"policy A (every word FLAG): would release {a} more -> released {released + a} ({100 * (released + a) / n:.0f}%)",
           f"policy B (no word HOLD):    would release {b} more -> released {released + b} ({100 * (released + b) / n:.0f}%)",
           "word tiers (caps-only messages): " + ", ".join(f"{k} {tiers[k]}" for k in R.CLASS_TIERS),
           "word signals: " + ", ".join(f"{k} {signals[k]}" for k in R.CLASS_SIGNALS),
           "HOLD words by signal: " + ", ".join(f"{k} {hold_sig[k]}" for k in R.CLASS_SHAPE + ("IDENT",)),
           "messages still held under B, by signal (a message can have several): "
           + ", ".join(f"{k} {msg_hold[k]}" for k in R.CLASS_SHAPE + ("IDENT",))]
    return out


def main(argv=None, protector=None, use_ner: bool = True, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drop", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--directory", required=True, type=Path)
    ap.add_argument("--vocab", type=Path)
    ap.add_argument("--ignore", type=Path)
    ap.add_argument("--phrases", type=Path)
    a = ap.parse_args(argv)
    try:
        S.check_read_paths(a.drop, a.state, a.vocab, a.ignore, a.phrases)
        vocab = R.load_wordlist(a.vocab) if a.vocab else frozenset()
        if a.phrases:
            try:
                vocab = vocab | R.load_phrases(a.phrases)
            except ValueError as exc:
                raise S.Refused(str(exc)) from None
        ignore = R.load_wordlist(a.ignore) if a.ignore else frozenset()
        directory = a.directory.read_text(encoding="utf-8")
        if use_ner:
            N.load()
            if not str(N.STATUS).startswith("active"):
                raise S.Refused("the NER layer is not active; real runs require it")
        res = S.scan_drop(a.drop, a.state, directory, vocab, ignore, use_ner=use_ner, protector=protector,
                          vocab_path=a.vocab, ignore_path=a.ignore, phrases_path=a.phrases)
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
