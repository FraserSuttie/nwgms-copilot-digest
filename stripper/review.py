"""Local review queue CLI for unknown names. Run by Fraser only, never by Claude.

    python stripper/review.py --state <DIR> list [--min 2] [--vocab F] [--ignore F]
    python stripper/review.py --state <DIR> classify "First Last" staff|external|ignore
    python stripper/review.py --state <DIR> triage [--min 2] [--vocab F] [--ignore F]
    python stripper/review.py --state <DIR> purge        # forget unknown-name counts, keep classifications

<DIR> must be a local folder outside the repo and outside OneDrive (default for the stripper:
%LOCALAPPDATA%\\nwgms-stripper). The state file is encrypted with Windows DPAPI; a plaintext state
file from slice 1 is migrated and removed on first open. Output is names and counts
only, never surrounding text. For `staff` it prints a People-Directory row for you to paste
yourself. Nothing is written to the vault.

--vocab / --ignore take the same local word files as the runner (residual.load_wordlist). Names whose every
word is already covered by them are hidden from list and triage. The files stay the source of truth: nothing
from them is copied into the state (ADR 0006).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import residual as R  # noqa: E402


def directory_row(name: str) -> str:
    parts = name.title().split()
    return f"| {parts[-1]} | {' '.join(parts)} | TBC | NWGMS | |"


def covered_words(vocab=None, ignore=None) -> frozenset:
    words = set()
    for f in (vocab, ignore):
        if f is not None:
            words |= {w.lower() for w in R.load_wordlist(f)}
    return frozenset(words)


def _covered(name: str, covered: frozenset) -> bool:
    toks = name.lower().split()
    return bool(toks) and all(t in covered for t in toks)


def cmd_list(st: G.State, min_count: int, covered=frozenset()) -> int:
    rows = sorted(((c, n) for n, c in st.data["counts"].items() if c >= min_count and not _covered(n, covered)),
                  reverse=True)
    for c, n in rows:
        print(f"{c:5d}  {n}")
    print(f"-- {len(rows)} name(s) with count >= {min_count}")
    return 0


def cmd_classify(st: G.State, name: str, kind: str) -> int:
    n = st.classify(name, kind)
    st.save()
    print(f"recorded: {n} -> {kind}")
    if kind == "staff":
        print("Paste into Stakeholders/People-Directory.md yourself:")
        print(directory_row(n))
    return 0


def cmd_triage(st: G.State, min_count: int, covered=frozenset()) -> int:
    for n, c in sorted(st.data["counts"].items(), key=lambda kv: -kv[1]):
        if c < min_count or _covered(n, covered):
            continue
        ans = input(f"{c:4d}  {n}  [s]taff / [e]xternal / [i]gnore / [enter]=skip / [q]uit: ").strip().lower()
        if ans == "q":
            break
        kind = {"s": "staff", "e": "external", "i": "ignore"}.get(ans)
        if kind:
            cmd_classify(st, n, kind)
    return 0


def main(argv=None, protector=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "triage"):
        sp = sub.add_parser(name)
        sp.add_argument("--min", type=int, default=2)
        sp.add_argument("--vocab", type=Path)
        sp.add_argument("--ignore", type=Path)
    sub.add_parser("purge")
    cp = sub.add_parser("classify")
    cp.add_argument("name")
    cp.add_argument("kind", choices=("staff", "external", "ignore"))
    a = ap.parse_args(argv)
    try:
        st = G.State(a.state, protector or K.default_protector())
    except (ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    covered = covered_words(getattr(a, "vocab", None), getattr(a, "ignore", None))
    if a.cmd == "list":
        return cmd_list(st, a.min, covered)
    if a.cmd == "purge":
        n = st.purge_counts()
        st.save()
        print(f"purged counts for {n} name(s)")
        return 0
    if a.cmd == "classify":
        return cmd_classify(st, a.name, a.kind)
    return cmd_triage(st, a.min, covered)


if __name__ == "__main__":
    sys.exit(main())
