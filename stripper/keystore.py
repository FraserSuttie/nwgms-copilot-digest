"""Local key store (Z3) for the content-lane stripper. Never reachable by Claude.

Layout (all inside one local state directory, outside the repo and outside OneDrive):

    master.key.dpapi   32-byte master secret, wrapped by the protector
    tables.dpapi       decode tables: REF/SMP/WS/QC/CASE codes, CONTACT map, ATT originals
    audit.log          one JSON line per message: keyed hash, decision, counts, version. No content.

Year keys are derived from the master and never stored:
    year_key(y) = HMAC-SHA256(master, "nwgms-stripper/v1/year/<y>")

Protection is a swappable "protector". On Windows the real one is DPAPI via ctypes (standard
library only): the files can only be opened by the same Windows login. There is deliberately no
plaintext default: a KeyStore cannot be built without naming a protector, and the only
non-Windows protector is `TestOnlyProtector`, used by tests in a temp directory.

`resolve()` exists for the future Fraser-run decode CLI and for synthetic tests. The stripper
pipeline never calls it, and Claude never runs it against a real store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import namegate as G  # noqa: E402  (check_state_dir)

ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # 20 consonants: no digits (cannot trip digit detectors), no vowels (cannot read as a name)
CODE_PREFIXES = ("REF", "SMP", "WS", "QC", "CASE")
START_LEN, MAX_LEN = 8, 16


# ---- protectors -------------------------------------------------------------------------------
class DpapiProtector:
    """Windows DPAPI (CryptProtectData / CryptUnprotectData) via ctypes. Current-user scope."""

    ENTROPY = b"nwgms-stripper/v1"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("DPAPI is only available on Windows; this run cannot protect a key store")

    @staticmethod
    def _blob(data: bytes):
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB

    def _call(self, fn_name: str, data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        b_in, keep_in, blob_t = self._blob(data)
        b_ent, keep_ent, _ = self._blob(self.ENTROPY)
        b_out = blob_t()
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        fn = getattr(crypt32, fn_name)
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(b_in), None, ctypes.byref(b_ent), None, None, 0x1, ctypes.byref(b_out))  # 0x1 = no UI
        if not ok:
            raise OSError(f"{fn_name} failed (error {ctypes.GetLastError()})")
        try:
            return ctypes.string_at(b_out.pbData, b_out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(b_out.pbData, ctypes.c_void_p))
            del keep_in, keep_ent

    def protect(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call("CryptUnprotectData", data)


class TestOnlyProtector:
    """NOT protection. Marks bytes so tests can run on any OS. Never used for a real store."""

    __test__ = False  # not a test class for unittest/pytest collection
    MARK = b"TESTONLY-NOT-ENCRYPTED:"

    def protect(self, data: bytes) -> bytes:
        return self.MARK + data

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(self.MARK):
            raise ValueError("not a test-only blob")
        return data[len(self.MARK):]


def default_protector():
    """The real protector for Fraser's laptop. Raises on non-Windows rather than falling back."""
    return DpapiProtector()


def default_state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise OSError("LOCALAPPDATA is not set; pass an explicit local state directory")
    return Path(base) / "nwgms-stripper"


# ---- key store --------------------------------------------------------------------------------
def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def normalise_value(value: str) -> str:
    """Identifier normal form: letters and digits only, upper case ('r26-ab12' and 'R26 AB12' agree)."""
    return "".join(ch for ch in value if ch.isalnum()).upper()


def _encode(digest: bytes, length: int) -> str:
    n = int.from_bytes(digest[:16], "big")
    out = []
    for _ in range(length):
        n, r = divmod(n, len(ALPHABET))
        out.append(ALPHABET[r])
    return "".join(out)


class KeyStore:
    MASTER, TABLES, AUDIT = "master.key.dpapi", "tables.dpapi", "audit.log"

    def __init__(self, path: Path, protector) -> None:
        if protector is None:
            raise ValueError("a protector is required: there is no plaintext key store")
        self.dir = G.check_state_dir(path)
        self.protector = protector
        mpath = self.dir / self.MASTER
        if mpath.exists():
            self._master = protector.unprotect(mpath.read_bytes())
        else:
            self._master = os.urandom(32)
            _atomic_write(mpath, protector.protect(self._master))
        tpath = self.dir / self.TABLES
        self.t = {"v": 1, "codes": {c: {} for c in CODE_PREFIXES}, "contacts": {"next": 1, "ids": {}}, "att": {}}
        if tpath.exists():
            loaded = json.loads(protector.unprotect(tpath.read_bytes()).decode("utf-8"))
            for k, v in loaded.items():
                self.t[k] = v
        self._tok = {(c, tok): key for c, d in self.t["codes"].items() for key, tok in d.items()}
        self.dirty = False

    # -- keys
    def year_key(self, year: int) -> bytes:
        return hmac.new(self._master, f"nwgms-stripper/v1/year/{int(year)}".encode(), hashlib.sha256).digest()

    def message_hash(self, text: str) -> str:
        """Keyed hash for audit and dedup: cannot be recomputed or confirmed without the master."""
        key = hmac.new(self._master, b"nwgms-stripper/v1/message", hashlib.sha256).digest()
        return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    # -- REF / SMP / WS / QC / CASE
    def token(self, category: str, value: str, year: int) -> str:
        """Deterministic token for an identifier. Collisions get a longer code, recorded in the table."""
        if category not in CODE_PREFIXES:
            raise ValueError(f"unknown category {category!r}")
        norm = normalise_value(value)
        key = f"{int(year)}|{norm}"
        table = self.t["codes"][category]
        if key in table:
            return f"{category}-{table[key]}"
        digest = hmac.new(self.year_key(year), f"{category}|{norm}".encode(), hashlib.sha256).digest()
        for length in range(START_LEN, MAX_LEN + 1, 2):
            code = _encode(digest, length)
            if (category, code) not in self._tok:
                table[key] = code
                self._tok[(category, code)] = key
                self.dirty = True
                return f"{category}-{code}"
        raise RuntimeError("could not find a collision-free code")  # astronomically unlikely

    # -- CONTACT-nnnn
    def contact(self, email: str = "", name: str = "") -> int:
        ids = self.t["contacts"]["ids"]
        keys = [k for k in (f"e:{email.strip().lower()}" if email.strip() else "",
                            f"n:{G.norm(name)}" if G.norm(name) else "") if k]
        if not keys:
            raise ValueError("a contact needs an email or a name")
        n = next((ids[k] for k in keys if k in ids), None)
        if n is None:
            n = self.t["contacts"]["next"]
            self.t["contacts"]["next"] = n + 1
        for k in keys:
            if ids.get(k) != n:
                ids[k] = n
                self.dirty = True
        return n

    def contact_alias(self, name: str) -> int | None:
        return self.t["contacts"]["ids"].get(f"n:{G.norm(name)}")

    # -- ATT-n originals
    def attachment(self, msg_hash: str, n: int, filename: str) -> None:
        self.t["att"].setdefault(msg_hash, {})[str(n)] = filename
        self.dirty = True

    # -- local-only resolution (future decode CLI; tests)
    def resolve(self, token: str) -> str | None:
        prefix, _, rest = token.partition("-")
        if prefix in CODE_PREFIXES:
            key = self._tok.get((prefix, rest))
            return key.split("|", 1)[1] if key else None
        if prefix == "CONTACT" and rest.isdigit():
            return ", ".join(sorted(k for k, v in self.t["contacts"]["ids"].items() if v == int(rest))) or None
        return None

    # -- persistence and audit
    def save(self) -> None:
        if self.dirty:
            _atomic_write(self.dir / self.TABLES, self.protector.protect(json.dumps(self.t, sort_keys=True).encode("utf-8")))
            self.dirty = False

    def audit(self, msg_hash: str, decision: str, counts: Counter, version: str) -> None:
        """Append one line: keyed message hash, decision, counts by category, version. No content."""
        line = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "msg": msg_hash,
                "decision": decision, "counts": dict(sorted(counts.items())), "version": version}
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / self.AUDIT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
