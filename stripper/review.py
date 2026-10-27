"""Local review queue CLI for unknown names. Run by Fraser only, never by Claude.

    python stripper/review.py --state <DIR> list [--min 2]
    python stripper/review.py --state <DIR> classify "First Last" staff|external|ignore
    python stripper/review.py --state <DIR> triage [--min 2]
    python stripper/review.py --state <DIR> purge        # forget unknown-name counts, keep classifications

<DIR> must be a local folder outside the repo and outside OneDrive (default for the stripper:
%LOCALAPPDATA%\\nwgms-stripper). The state file is encrypted with Windows DPAPI; a plaintext state
file from slice 1 is migrated and removed on first open. Output is names and counts
only, never surrounding text. For `staff` it prints a People-Directory row for you to paste
yourself. Nothing is written to the vault.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402


def directory_row(name: str) -> str:
    parts = name.title().split()
    return f"| {parts[-1]} | {' '.join(parts)} | TBC | NWGMS | |"


def cmd_list(st: G.State, min_count: int) -> int:
    rows = sorted(((c, n) for n, c in st.data["counts"].items() if c >= min_count), reverse=True)
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


def cmd_triage(st: G.State, min_count: int) -> int:
    for n, c in sorted(st.data["counts"].items(), key=lambda kv: -kv[1]):
        if c < min_count:
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
    if a.cmd == "list":
        return cmd_list(st, a.min)
    if a.cmd == "purge":
        n = st.purge_counts()
        st.save()
        print(f"purged counts for {n} name(s)")
        return 0
    if a.cmd == "classify":
        return cmd_classify(st, a.name, a.kind)
    return cmd_triage(st, a.min)


if __name__ == "__main__":
    sys.exit(main())
