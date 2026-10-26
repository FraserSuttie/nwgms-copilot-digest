#!/usr/bin/env python3
"""Deterministic validator for Copilot digest files (G2 in docs/spec.md).

Usage:
    python validate_digest.py FILE.json [--lenient] [--json]
                              [--topics topics.txt] [--people people.txt]

Exit codes:
    0  file passes
    1  file fails (quarantine it)
    2  usage or I/O error

The report names the field and the rule that fired. It never prints the value
that triggered a finding, so a leak is not copied into logs or terminals.

Standard library only. No LLM is involved in any decision.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patterns as P  # noqa: E402

MAX_BYTES = 64 * 1024
MAX_ENTRIES = 50
MAX_TEXT = 60

TOP_KEYS = {"schema_version", "generated", "lane", "entries"}
ENTRY_REQUIRED = {"source", "received", "counterpart", "systems", "request_type", "action_needed", "direction"}
ENTRY_OPTIONAL = {"refs", "due", "topic"}

_NAME_PUNCT = set(" .'’-,()/&")
_TOPIC_RE = re.compile(r"[A-Za-z0-9 _\-/&.]{1,%d}" % MAX_TEXT)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # "fail" or "review"
    where: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "where": self.where}


class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise _DuplicateKey(k)
        seen[k] = v
    return seen


def _bad_chars(text: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn") for c in text)


def _scan(where: str, text: str, out: list[Finding]) -> None:
    if _bad_chars(text):
        out.append(Finding("CONTROL_CHAR", "fail", where))
    for h in P.scan_text(text):
        out.append(Finding(h.code, h.severity, where))


def _name_ok(text: str) -> bool:
    return bool(text) and len(text) <= MAX_TEXT and all(c.isalpha() or c in _NAME_PUNCT for c in text)


def _read_list(path: str | None) -> set[str] | None:
    if not path:
        return None
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def validate(raw: bytes, topics: set[str] | None = None, people: set[str] | None = None) -> list[Finding]:
    out: list[Finding] = []

    if len(raw) > MAX_BYTES:
        return [Finding("FILE_TOO_LARGE", "fail", "file")]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding("FILE_ENCODING", "fail", "file")]
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicates)
    except _DuplicateKey:
        return [Finding("DUPLICATE_KEY", "fail", "file")]
    except ValueError:
        return [Finding("INVALID_JSON", "fail", "file")]

    if not isinstance(doc, dict):
        return [Finding("SCHEMA_TOP", "fail", "file")]

    # Unknown keys are findings, and their names are scanned too.
    for k in doc:
        if k not in TOP_KEYS:
            out.append(Finding("SCHEMA_UNKNOWN_KEY", "fail", "top"))
            _scan("top.key", str(k), out)
    if doc.get("schema_version") != "1":
        out.append(Finding("SCHEMA_VERSION", "fail", "schema_version"))
    lane = doc.get("lane")
    if lane not in P.LANES:
        out.append(Finding("SCHEMA_LANE", "fail", "lane"))
    generated = doc.get("generated")
    if not (isinstance(generated, str) and P.RECEIVED_RE.fullmatch(generated)):
        out.append(Finding("SCHEMA_GENERATED", "fail", "generated"))

    entries = doc.get("entries")
    if not isinstance(entries, list):
        out.append(Finding("SCHEMA_ENTRIES", "fail", "entries"))
        return out
    if len(entries) > MAX_ENTRIES:
        out.append(Finding("TOO_MANY_ENTRIES", "fail", "entries"))
        entries = entries[:MAX_ENTRIES]

    lane_sources, lane_dirs = P.LANES.get(lane, ((), ()))

    for i, e in enumerate(entries):
        w = f"entries[{i}]"
        if not isinstance(e, dict):
            out.append(Finding("SCHEMA_ENTRY", "fail", w))
            continue

        for k in e:
            if k not in ENTRY_REQUIRED | ENTRY_OPTIONAL:
                out.append(Finding("SCHEMA_UNKNOWN_KEY", "fail", w))
                _scan(f"{w}.key", str(k), out)
        for k in sorted(ENTRY_REQUIRED - set(e)):
            out.append(Finding("SCHEMA_MISSING", "fail", f"{w}.{k}"))

        def choice(field: str, allowed, extra=None):
            v = e.get(field)
            if field in e and (not isinstance(v, str) or v not in allowed or (extra is not None and v not in extra)):
                out.append(Finding("SCHEMA_VALUE", "fail", f"{w}.{field}"))
                if isinstance(v, str):
                    _scan(f"{w}.{field}", v, out)

        choice("source", P.SOURCES, lane_sources if lane in P.LANES else None)
        choice("request_type", P.REQUEST_TYPES)
        choice("action_needed", P.ACTIONS)
        choice("direction", P.DIRECTIONS, lane_dirs if lane in P.LANES else None)

        # Dates are checked by strict shape, then excluded from the general scan.
        r = e.get("received")
        if "received" in e and not (isinstance(r, str) and P.RECEIVED_RE.fullmatch(r)):
            out.append(Finding("SCHEMA_RECEIVED", "fail", f"{w}.received"))
            if isinstance(r, str):
                _scan(f"{w}.received", r, out)
        d = e.get("due")
        if d is not None and not (isinstance(d, str) and P.DUE_RE.fullmatch(d)):
            out.append(Finding("SCHEMA_DUE", "fail", f"{w}.due"))
            if isinstance(d, str):
                _scan(f"{w}.due", d, out)

        cp = e.get("counterpart")
        if "counterpart" in e:
            if not (isinstance(cp, str) and _name_ok(cp)):
                out.append(Finding("SCHEMA_COUNTERPART", "fail", f"{w}.counterpart"))
            if isinstance(cp, str):
                _scan(f"{w}.counterpart", cp, out)
                if people is not None and not any(cp == n or cp.startswith(n + ",") or cp.startswith(n + " (") for n in people):
                    out.append(Finding("COUNTERPART_UNKNOWN", "review", f"{w}.counterpart"))

        systems = e.get("systems")
        if "systems" in e:
            if not (isinstance(systems, list) and 1 <= len(systems) <= 4 and all(isinstance(s, str) and s in P.SYSTEMS for s in systems)):
                out.append(Finding("SCHEMA_SYSTEMS", "fail", f"{w}.systems"))
                for s in systems if isinstance(systems, list) else []:
                    if isinstance(s, str):
                        _scan(f"{w}.systems", s, out)

        refs = e.get("refs", [])
        if not isinstance(refs, list) or len(refs) > 5:
            out.append(Finding("SCHEMA_REFS", "fail", f"{w}.refs"))
        else:
            for ref in refs:
                if isinstance(ref, str) and P.is_allowed_ref(ref):
                    continue  # exact allowed shape; cannot carry an identifier
                out.append(Finding("SCHEMA_REF_NOT_ALLOWED", "fail", f"{w}.refs"))
                if isinstance(ref, str):
                    _scan(f"{w}.refs", ref, out)

        tp = e.get("topic")
        if tp is not None:
            if not (isinstance(tp, str) and _TOPIC_RE.fullmatch(tp)):
                out.append(Finding("SCHEMA_TOPIC", "fail", f"{w}.topic"))
            if isinstance(tp, str):
                _scan(f"{w}.topic", tp, out)
                if topics is not None and tp not in topics:
                    out.append(Finding("TOPIC_UNKNOWN", "review", f"{w}.topic"))

    # keep first occurrence of each (code, where)
    seen, uniq = set(), []
    for f in out:
        key = (f.code, f.where)
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def exit_code(findings: list[Finding], lenient: bool = False) -> int:
    blocking = [f for f in findings if f.severity == "fail" or not lenient]
    return 1 if blocking else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a Copilot digest file.")
    ap.add_argument("file")
    ap.add_argument("--lenient", action="store_true", help="let 'review' findings pass (tuning aid)")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--topics", help="text file of allowed topic names, one per line")
    ap.add_argument("--people", help="text file of known counterpart names, one per line")
    args = ap.parse_args(argv)
    try:
        raw = Path(args.file).read_bytes()
        topics, people = _read_list(args.topics), _read_list(args.people)
    except OSError as exc:
        print(f"ERROR: cannot read input ({exc.__class__.__name__})", file=sys.stderr)
        return 2

    findings = validate(raw, topics, people)
    code = exit_code(findings, args.lenient)
    nf = sum(f.severity == "fail" for f in findings)
    nr = len(findings) - nf
    if args.json:
        print(json.dumps({"result": "FAIL" if code else "PASS", "fail": nf, "review": nr,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    else:
        for f in findings:
            print(f"{f.severity.upper():6} {f.where:28} {f.code}")
        print(f"RESULT: {'FAIL' if code else 'PASS'} ({nf} fail, {nr} review)")
    return code


if __name__ == "__main__":
    sys.exit(main())
