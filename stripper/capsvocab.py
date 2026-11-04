"""Local vocabulary builder for the unknown-caps check (ADR 0010). Run by Fraser only, in a terminal, never by Claude.

    python stripper/capsvocab.py --drop D --state S --directory P --vocab F [--ignore F] [--min-threads 2] [--top 200]
    python stripper/capsvocab.py ... --list       # print only, writes nothing

It shows the unknown capitalised words that block messages today, ranked by the number of THREADS they appear in, and
lets you allow them one at a time. The words go to this terminal only: stdout must be a terminal (and stdin, to allow).
The only file it ever writes is --vocab, by appending the words you allow, one per line. --vocab must be local: not in
the repo, OneDrive, a vault, or the drop. Nothing else is written (no state, quarantine, audit, output).

A word you allow is no longer checked in ANY message. Do not allow a forename or surname. Flags:
  lc    also seen in lower case in the drop (likely an ordinary word)
  1t    appears in one thread only (a patient surname repeated in one thread looks frequent by message count)
  cue   appears within two words after patient / pt / Mr / Mrs ... (REVIEW CAREFULLY)
  ext   is a word of an external participant's display name (REVIEW CAREFULLY)
Flagged cue / ext words are listed last and need the word typed in full to allow.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capsscan as S  # noqa: E402
import ner as N  # noqa: E402
import residual as R  # noqa: E402
import runner as RN  # noqa: E402

Refused = S.Refused


def check_vocab_target(vocab: Path, drop: Path) -> Path:
    v, d = RN._resolved(vocab), RN._resolved(drop)
    if RN._in_repo(v) or RN._in_onedrive(v):
        raise Refused("the vocabulary file must be local: not inside the repo or OneDrive")
    if RN._vault_root(v):
        raise Refused("the vocabulary file must not be inside a vault")
    if v == d or d in v.parents:
        raise Refused("the vocabulary file must not be inside the drop")
    if v.is_dir() or not v.parent.is_dir():
        raise Refused("the vocabulary file needs an existing folder and must not be a folder")
    return v


def append_word(path: Path, word: str) -> None:
    """Append one word on its own line. The only write this tool makes."""
    prefix = ""
    if path.exists() and path.stat().st_size:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(prefix + word + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def candidates(stats: dict, min_threads: int, top: int, show_all: bool) -> tuple[list, list]:
    key = lambda w: (-len(w.threads), -w.msgs, w.key)  # noqa: E731
    keep = [w for w in stats.values() if show_all or len(w.threads) >= min_threads]
    main_ = sorted((w for w in keep if not w.flagged), key=key)
    flagged = sorted((w for w in keep if w.flagged), key=key)
    return main_[:top], flagged[:max(0, top - len(main_))]


def describe(w) -> str:
    sec = "".join(sorted({"subject": "S", "body": "B"}.get(s, "H") for s in w.sections))
    pos = "init" if not w.mid else "mid" if not w.initial else "both"
    flags = " ".join(f for f, on in (("lc", w.lower), ("1t", len(w.threads) == 1), ("cue", w.cue), ("ext", w.ext)) if on)
    return f"threads {len(w.threads):3d}  msgs {w.msgs:3d}  occ {w.occ:3d}  {sec:<3} {pos:<4}  {flags:<12}{w.shown}"


def run(res: S.ScanResult, vocab: Path | None, out, inp, min_threads: int, top: int, show_all: bool, list_only: bool) -> dict:
    stats = S.word_stats(res)
    co = S.caps_only(res)
    main_, flagged = candidates(stats, min_threads, top, show_all)
    summary = {"shown": len(main_) + len(flagged), "allowed": 0, "skipped": 0}
    print(f"-- {len(co)} message(s) are blocked by unknown capitalised words only; "
          f"{len(stats)} distinct word(s); {len(main_)} listed, then {len(flagged)} to review carefully.", file=out)
    print("-- A word you allow is no longer checked in any message. Do not allow a forename or surname.", file=out)
    accepted: set = set()
    rows = [(w, False) for w in main_] + [(w, True) for w in flagged]
    if list_only:
        for i, (w, careful) in enumerate(rows, 1):
            print(f"{i:4d}{'*' if careful else ' '} {describe(w)}", file=out)
        print("-- * = review carefully (cue / ext). Nothing was written.", file=out)
        return summary
    quit_ = False
    for i, (w, careful) in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}]{' REVIEW CAREFULLY' if careful else ''} {describe(w)}", file=out)
        if careful:
            print("   type the word in full to allow, Enter to skip, q to quit: ", end="", file=out, flush=True)
        else:
            print("   [a]llow / Enter = skip / [q]uit: ", end="", file=out, flush=True)
        ans = inp.readline()
        if ans == "":
            ans = "q"
        ans = ans.strip()
        if ans.lower() == "q":
            quit_ = True
            break
        ok = ans.lower() == w.key if careful else ans.lower() in ("a", "allow", "y")
        if ok:
            append_word(vocab, w.shown)
            accepted.add(w.key)
            summary["allowed"] += 1
            print(f"   allowed. Projected: {S.would_clear(co, accepted)} of {len(co)} blocked message(s) would clear "
                  f"(approximate, rerun to confirm).", file=out)
        else:
            summary["skipped"] += 1
    print(f"-- allowed {summary['allowed']}, skipped {summary['skipped']}"
          f"{', stopped early' if quit_ else ''}. Projected clear: {S.would_clear(co, accepted)} of {len(co)}.", file=out)
    return summary


def main(argv=None, protector=None, use_ner: bool = True, out=sys.stdout, inp=sys.stdin) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drop", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--directory", required=True, type=Path)
    ap.add_argument("--vocab", type=Path, help="local vocabulary file to read and append to (required unless --list)")
    ap.add_argument("--ignore", type=Path)
    ap.add_argument("--min-threads", type=int, default=2)
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--all", action="store_true", help="include words seen in only one thread")
    ap.add_argument("--list", action="store_true", help="print the list and write nothing")
    a = ap.parse_args(argv)
    try:
        if not (hasattr(out, "isatty") and out.isatty()):
            raise Refused("stdout is not a terminal: the words are shown in a terminal only")
        if not a.list and not (hasattr(inp, "isatty") and inp.isatty()):
            raise Refused("stdin is not a terminal: allowing words is interactive (use --list to print only)")
        if not a.list and a.vocab is None:
            raise Refused("--vocab FILE is required to allow words")
        S.check_read_paths(a.drop, a.state, a.vocab, a.ignore)
        vocab_path = check_vocab_target(a.vocab, a.drop) if a.vocab else None
        vocab = R.load_wordlist(vocab_path) if vocab_path and vocab_path.exists() else frozenset()
        ignore = R.load_wordlist(a.ignore) if a.ignore else frozenset()
        directory = a.directory.read_text(encoding="utf-8")
        if use_ner:
            N.load()
            if not str(N.STATUS).startswith("active"):
                raise Refused("the NER layer is not active; real runs require it")
        res = S.scan_drop(a.drop, a.state, directory, vocab, ignore, use_ner=use_ner, protector=protector,
                          vocab_path=a.vocab, ignore_path=a.ignore)
        run(res, vocab_path, out, inp, a.min_threads, a.top, a.all, a.list)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never print an exception message: it could carry content
        print(f"error: {type(exc).__name__} (message suppressed)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
