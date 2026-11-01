"""Quarantine store (Z4) for the content-lane stripper. Never reachable by Claude.

Layout, inside the local state directory (outside the repo and outside OneDrive):

    quarantine/<keyed-message-hash>.q   the WHOLE raw message, protected by the same protector as the key store
    quarantine/index.jsonl              one JSON line per message: time, keyed hash, reason-code COUNTS, version

The stripped attempt is never stored. The index holds no content and no values.

CLI (Fraser only; prints counts only, never message content):
    python stripper/quarantine.py list
    python stripper/quarantine.py purge [--all | --hash <keyed-hash>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402


class QuarantineError(RuntimeError):
    """The quarantine write failed. The pipeline must stop: it never releases a message it could not park."""


class Quarantine:
    INDEX = "index.jsonl"

    def __init__(self, state_dir: Path, protector) -> None:
        if protector is None:
            raise ValueError("a protector is required: quarantined messages are never stored in plaintext")
        self.dir = G.check_state_dir(Path(state_dir)) / "quarantine"
        self.protector = protector

    def put(self, msg_hash: str, message: dict, reasons: Counter, version: str) -> None:
        """Store the whole raw message and index its reason codes. Raises QuarantineError on any failure."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            blob = self.protector.protect(json.dumps(message, sort_keys=True).encode("utf-8"))
            K._atomic_write(self.dir / f"{msg_hash}.q", blob)
            line = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "msg": msg_hash,
                    "reasons": dict(sorted(reasons.items())), "version": version}
            with open(self.dir / self.INDEX, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
        except Exception as exc:  # noqa: BLE001
            raise QuarantineError(f"quarantine write failed ({type(exc).__name__})") from None

    def summary(self) -> tuple[int, Counter]:
        """(messages, reason-code counts) from the index. No content."""
        total, reasons = 0, Counter()
        idx = self.dir / self.INDEX
        if idx.exists():
            for line in idx.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    total += 1
                    reasons.update(json.loads(line)["reasons"])
        return total, reasons

    def purge(self, msg_hash: str | None = None) -> int:
        """Delete one message (by keyed hash) or all. Returns the number of messages removed."""
        if not self.dir.exists():
            return 0
        files = [self.dir / f"{msg_hash}.q"] if msg_hash else sorted(self.dir.glob("*.q"))
        n = 0
        for f in files:
            if f.exists():
                f.unlink()
                n += 1
        idx = self.dir / self.INDEX
        if idx.exists():
            if msg_hash:
                keep = [l for l in idx.read_text(encoding="utf-8").splitlines() if l.strip() and json.loads(l)["msg"] != msg_hash]
                idx.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
            else:
                idx.unlink()
        return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Quarantine store: counts only")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("purge")
    p.add_argument("--all", action="store_true")
    p.add_argument("--hash")
    a = ap.parse_args()
    q = Quarantine(K.default_state_dir(), K.default_protector())
    if a.cmd == "list":
        total, reasons = q.summary()
        print(f"quarantined messages: {total}")
        for k, v in sorted(reasons.items()):
            print(f"  {k:24}{v:6d}")
    elif a.all or a.hash:
        print(f"purged {q.purge(None if a.all else a.hash)} message(s)")
    else:
        ap.error("purge needs --all or --hash")


if __name__ == "__main__":
    main()
