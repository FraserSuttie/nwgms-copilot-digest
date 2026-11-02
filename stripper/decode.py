"""Local decode CLI (ADR 0007). Run by Fraser only, never by Claude, never on a Claude-visible terminal.

    python stripper/decode.py [--state DIR] TOKEN [TOKEN ...]
    python stripper/decode.py [--state DIR] --thread PATH\\THR-XXXXXXXX.md

Tokens: REF-, SMP-, WS-, QC-, CASE- codes, CONTACT-nnnn, and ATT-n@HASH where HASH is at least the 12 hex
characters shown in the block heading ("### Message 3f9a1c2b7d0e"). --thread prints a lookup table of every
decodable token in that thread file (token | kind | value) and never the message text.

Rules:
- Output goes to the terminal only. Redirected or piped stdout is refused; nothing is written to disk except one
  audit line (tokens asked for, counts, refused count; never a value).
- Patient placeholders ([PT-*], [NAME-n], [CLIN], [DATE], [LOC], [URL], [GMC-ID]) and THR- keys cannot be
  decoded: there is no map, by design.
- --state must be local (not the repo, not OneDrive); default %LOCALAPPDATA%\\nwgms-stripper.
- --thread must be a stripper thread file (THR-*.md, written-by nwgms-stripper), not inside the repo, <= 5 MB.
Exit codes: 0 all resolved, 1 something refused, 2 usage, path or terminal error.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import transform as T  # noqa: E402

VERSION = T.STRIPPER_VERSION
MAX_THREAD_BYTES = 5 * 1024 * 1024
_C = K.ALPHABET

CODE = re.compile(rf"^(REF|SMP|WS|QC|CASE)-([{_C}]{{8,16}})$")
CONTACT = re.compile(r"^CONTACT-(\d{4,})$")
ATT = re.compile(r"^ATT-(\d{1,3})@([0-9a-f]{12,32})$")
NO_MAP = re.compile(rf"^(?:\[(?:PT-[A-Z]+|NAME-\d+|CLIN|DATE|LOC|URL|GMC-ID)\]|THR-[{_C}]{{8}})$")
KINDS = {"REF": "referral", "SMP": "sample", "WS": "worksheet", "QC": "qc", "CASE": "case",
         "CONTACT": "contact", "ATT": "attachment"}

# thread-file scanning
_BLOCK = re.compile(r"<!-- block:([0-9a-f]{32}) -->\n(.*?)<!-- /block:\1 -->", re.S)
_FILE_TOKEN = re.compile(rf"\b(?:(?:REF|SMP|WS|QC|CASE)-[{_C}]{{8,16}}|CONTACT-\d{{4,}}|ATT-\d+)\b")
_FILE_NO_MAP = re.compile(r"\[(PT-[A-Z]+|NAME-\d+|CLIN|DATE|LOC|URL|GMC-ID)\]")


class Refused(ValueError):
    """A path, terminal or usage error. The message never contains a decoded value or file content."""


# ---- resolution -----------------------------------------------------------------------------------------------
def _contact(raw: str | None) -> str | None:
    if not raw:
        return None
    emails = [k[2:] for k in raw.split(", ") if k.startswith("e:")]
    names = [k[2:] for k in raw.split(", ") if k.startswith("n:")]
    return " ".join([*(n.title() for n in names), *(f"<{e}>" for e in emails)]) or None


def resolve(store: K.KeyStore, token: str) -> tuple[str, str]:
    """('ok', value) or ('refused', reason). The reason never contains a value."""
    tok = token.strip()
    if NO_MAP.match(tok):
        return "refused", "not decodable: no map by design"
    if m := CODE.match(tok):
        v = store.resolve(tok)
        return ("ok", v) if v else ("refused", "not in the key store")
    if CONTACT.match(tok):
        v = _contact(store.resolve(f"CONTACT-{int(tok.split('-')[1])}"))
        return ("ok", v) if v else ("refused", "not in the key store")
    if m := ATT.match(tok):
        status, v = store.resolve_attachment(m.group(2), int(m.group(1)))
        if status == "ambiguous":
            return "refused", "ambiguous block hash: give more characters"
        return ("ok", v) if v else ("refused", "not in the key store")
    if tok.startswith("ATT-"):
        return "refused", "malformed: ATT needs @HASH (12+ hex characters from the block heading)"
    return "refused", "malformed token"


def _prefix(tok: str) -> str:
    return tok.split("-", 1)[0] if not tok.startswith("[") else "PLACEHOLDER"


# ---- thread file ------------------------------------------------------------------------------------------------
def check_thread(path) -> Path:
    p = Path(path).expanduser().resolve()
    if p == G.REPO_ROOT or G.REPO_ROOT in p.parents:
        raise Refused("thread file must not be inside the repo")
    if not re.fullmatch(rf"THR-[{_C}]{{8}}\.md", p.name) or not p.is_file():
        raise Refused("not a thread file (THR-XXXXXXXX.md)")
    if p.stat().st_size > MAX_THREAD_BYTES:
        raise Refused("thread file is over the size limit")
    head = p.read_text(encoding="utf-8", errors="replace")[:600]
    if not head.startswith("---\n") or "written-by: nwgms-stripper" not in head:
        raise Refused("not a stripper thread file (frontmatter missing)")
    return p


def thread_tokens(text: str) -> tuple[list[str], Counter]:
    """Decodable tokens in first-appearance order (ATT qualified by block hash) and no-map placeholder counts."""
    seen, order, no_map = set(), [], Counter()
    for m in _BLOCK.finditer(text):
        h, block = m.group(1), m.group(2)
        for t in _FILE_TOKEN.findall(block):
            q = f"{t}@{h[:12]}" if t.startswith("ATT-") else t
            if q not in seen:
                seen.add(q)
                order.append(q)
        for p in _FILE_NO_MAP.findall(block):
            no_map["[PT-*]" if p.startswith("PT-") else "[NAME-n]" if p.startswith("NAME-") else f"[{p}]"] += 1
    return order, no_map


# ---- CLI --------------------------------------------------------------------------------------------------------
def _row(cells) -> str:
    return " | ".join(cells)


def run(argv, out=None, protector=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="decode.py", description="Local decode (ADR 0007). Terminal only.")
    ap.add_argument("--state", help="local state dir (default %%LOCALAPPDATA%%\\nwgms-stripper)")
    ap.add_argument("--thread", help="stripper thread file: print its token lookup table")
    ap.add_argument("tokens", nargs="*")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        if bool(a.thread) == bool(a.tokens):
            raise Refused("give one or more tokens, or --thread FILE (not both)")
        if not (hasattr(out, "isatty") and out.isatty()):
            raise Refused("stdout is not a terminal: decode output is terminal-only (ADR 0007)")
        state = Path(a.state) if a.state else K.default_state_dir()
        try:
            G.check_state_dir(state)
        except ValueError as e:
            raise Refused(str(e)) from None
        if not (state / K.KeyStore.MASTER).is_file():
            raise Refused("no key store in the state directory")
        thread = check_thread(a.thread) if a.thread else None
        store = K.KeyStore(state, protector if protector is not None else K.default_protector())
    except Refused as e:
        print(f"refused: {e}", file=out)
        return 2
    except Exception as e:  # noqa: BLE001 - never echo content
        print(f"error: {type(e).__name__}", file=out)
        return 2

    no_map = Counter()
    if thread:
        tokens, no_map = thread_tokens(thread.read_text(encoding="utf-8", errors="replace"))
        mode = "thread"
    else:
        tokens, mode = [t.strip() for t in a.tokens], "tokens"

    resolved, refused, rows = Counter(), 0, []
    for tok in tokens:
        status, v = resolve(store, tok)
        if status == "ok":
            resolved[_prefix(tok)] += 1
            rows.append((tok, KINDS.get(_prefix(tok), "?"), v))
        else:
            refused += 1
            rows.append((tok, "refused", v))

    if mode == "thread":
        print(_row(("token", "kind", "value")), file=out)
        print(_row(("---", "---", "---")), file=out)
    for r in rows:
        print(_row(r), file=out)
    if mode == "thread":
        if not rows:
            print("(no decodable tokens in this thread)", file=out)
        nm = ", ".join(f"{k} {v}" for k, v in sorted(no_map.items()))
        print(f"-- not decodable (no map by design): {nm or 'none'}", file=out)
    print(f"-- resolved {sum(resolved.values())}, refused {refused}", file=out)
    store.audit_decode(mode, tokens, resolved, refused, VERSION)
    return 1 if refused else 0


def main(argv=None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
