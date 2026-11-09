"""Name gate: decide which person-name spans pass through and which become [NAME-n].

Rules (Comms-Intake-Design section 6, plus Fraser's decisions of 27 Oct 2026):
- A full name passes if it is in the People-Directory, the header participants (From/To/Cc with
  Trust-domain addresses, display names), or the local allowlist (review queue).
- A bare first name or surname passes only if it identifies exactly ONE known person AND it is
  in a salutation, sign-off or speaker-label position, or that person's full name also passed
  elsewhere in the same message. Ambiguous or mid-sentence bare names are stripped.
- Everything else is stripped as [NAME-n]: numbered per message, same name same number, one-way.
- Decisions carry positions and reason codes only. Unknown names are counted in the review store.

No network, no AI. The state directory (allowlist, review counts) is local and must not live in
the repo or in a OneDrive-synced folder: see check_state_dir.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator"))
import patterns as P  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
TITLES = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "professor", "sir", "doctor"}
_GREETING = re.compile(
    r"(?:(?:hi|hello|hey|dear|morning|good\s+morning|good\s+afternoon|thanks|thank\s+you|many\s+thanks|cheers|"
    r"regards|kind\s+regards|best\s+regards|best\s+wishes|best|ta)\b[\s,]*)+", re.I)
_CAP_WORD = re.compile(r"\b[A-Z][A-Za-z'’\-]{2,}\b")


def norm(name: str) -> str:
    """Casefold, drop titles and punctuation, collapse spaces. 'Surname, First' becomes 'first surname'."""
    name = name.replace("’", "'")
    if "," in name:
        a, _, b = name.partition(",")
        name = f"{b} {a}"
    toks = [re.sub(r"[^\w'\-]", "", t) for t in name.split()]
    toks = [t for t in toks if t]
    while toks and toks[0].casefold().rstrip(".") in TITLES:
        toks.pop(0)
    return " ".join(toks).casefold()


@dataclass
class People:
    """Known staff. `full` maps normalised full name -> source tag (DIR, HEADER, LOCAL)."""
    full: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)  # first/last token -> set of canonical full names
    canon: dict = field(default_factory=dict)   # other-order variant -> canonical full name (ADR 0009)

    def copy(self) -> "People":
        return People(dict(self.full), {k: set(v) for k, v in self.tokens.items()}, dict(self.canon))

    def add(self, name: str, source: str, both_orders: bool = False, label: bool = False) -> None:
        """`both_orders`: also accept 'second first' for a two-token name (Trust 'Surname Forename' display names);
        tokens stay on ONE canonical name so a surname never maps to two strings (that would read as ambiguous).
        `label`: a non-person display name (shared mailbox): exact string only, no tokens."""
        n = norm(name)
        parts = n.split()
        if len(parts) < 2 or any(ch.isdigit() for ch in n):
            return
        if label:
            self.full.setdefault(n, source)
            return
        rev = " ".join(reversed(parts)) if both_orders and len(parts) == 2 else None
        if rev and rev != n:
            known = n if n in self.full else rev if rev in self.full else None
            if known:                       # same person already known (e.g. the Directory): add the other order only
                c = self.canon.get(known, known)
                for v in (n, rev):
                    if v != c:
                        self.full.setdefault(v, source)
                        self.canon[v] = c
                return
            self.full.setdefault(rev, source)
            self.canon[rev] = n
        self.full.setdefault(n, source)
        for t in {parts[0], parts[-1]}:
            self.tokens.setdefault(t, set()).add(n)

    def add_alias(self, variant: str, canonical: str, source: str) -> None:
        """A nickname form ('Cath Ramsden') of a name already added: same person, its first token becomes a name token."""
        n, c = norm(variant), norm(canonical)
        if c not in self.full or len(n.split()) < 2 or n == c:
            return
        self.full.setdefault(n, source)
        self.canon[n] = c
        self.tokens.setdefault(n.split()[0], set()).add(c)

    def with_header(self, header_people, trusted=P.ALLOWED_EMAIL_DOMAINS) -> "People":
        """Copy of self plus header participants whose address is on a Trust domain."""
        out = self.copy()
        for display, addr in header_people:
            dom = addr.rsplit("@", 1)[-1].lower()
            if any(dom == d or dom.endswith("." + d) for d in trusted):
                two = len(norm(display).split()) == 2     # anything else is a label (shared mailbox): exact string only
                out.add(display, "HEADER", both_orders=two, label=not two)
        return out


INTERNAL_ORG = re.compile(r"\s*(?:NWGMS|MFT)\b", re.I)
_BRACKETED = re.compile(r"\s*[(\[][^)\]]*[)\]]")


def parse_directory(markdown: str) -> People:
    """Read the People-Directory table (Surname | Full name | Role | Organisation | ...).

    ADR 0014: only rows whose Organisation starts with NWGMS or MFT are staff; other trusts, suppliers and TBC rows
    are left out, so their names are treated as unknown. A table with no Organisation column is read as all staff
    (older format). A round-bracketed nickname ('Catherine (Cath) Ramsden') becomes a name token for that row;
    square-bracketed notes ('[surname unknown]') are dropped."""
    ppl = People()
    org_col = None
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        if cells[0].lower() == "surname":
            low = [c.lower() for c in cells]
            org_col = low.index("organisation") if "organisation" in low else None
            continue
        if org_col is not None and not INTERNAL_ORG.match(cells[org_col] if org_col < len(cells) else ""):
            continue
        full = cells[1]
        base = _BRACKETED.sub("", full)
        ppl.add(base, "DIR")
        last = norm(base).split()[-1:] or [""]
        for nick in re.findall(r"\(([^)]*)\)", full):
            if len(nick.split()) == 1:
                ppl.add_alias(f"{nick} {last[0]}", base, "DIR")
    return ppl


@dataclass(frozen=True)
class Decision:
    start: int
    end: int
    action: str   # "pass" or "name"
    reason: str
    n: int = 0    # NAME number for action == "name"


def _position_ok(text: str, s: int, e: int) -> str:
    """Return a reason if a bare name at [s, e) sits in a greeting, sign-off or speaker-label position."""
    ls = text.rfind("\n", 0, s) + 1
    le = text.find("\n", e)
    le = len(text) if le < 0 else le
    prefix, suffix = text[ls:s], text[e:le]
    if prefix.strip() and _GREETING.fullmatch(prefix.strip() + " "):
        return "BARE_GREETING"
    if not prefix.strip():
        if suffix.lstrip().startswith(":"):
            return "BARE_SPEAKER"
        if re.fullmatch(r"\s*[,.!]?\s*", suffix):
            return "BARE_SIGNOFF"
    return ""


def gate(text: str, ner_spans, people: People, ignore=frozenset(), review: Counter | None = None):
    """Decide each person-name candidate. `ner_spans` is a list of (start, end) from the NER layer.

    Candidates also include capitalised words equal to a known first name or surname that the NER
    layer missed. Returns (decisions sorted by start, Counter of reason codes).
    """
    spans = []
    for a, b in sorted(set(ner_spans)):  # NER spans can run across line breaks: gate each line piece
        pos = a
        for piece in text[a:b].split("\n"):
            lo = pos + (len(piece) - len(piece.lstrip()))
            hi = pos + len(piece.rstrip())
            if hi > lo:
                spans.append((lo, hi))
            pos += len(piece) + 1
    # Known first names / surnames. Adjacent known words form one run; a run that overlaps NER spans
    # replaces them with their union, so "Priya Lindqvist" is never gated as two bare names.
    runs, run = [], None
    for m in _CAP_WORD.finditer(text):
        known = norm(m.group(0)) in people.tokens
        if known and run and text[run[1]:m.start()] == " ":
            run = (run[0], m.end())
            continue
        if run:
            runs.append(run)
        run = (m.start(), m.end()) if known else None
    if run:
        runs.append(run)
    for r0, r1 in runs:
        over = [sp for sp in spans if r0 < sp[1] and sp[0] < r1]
        spans = [sp for sp in spans if sp not in over]
        spans.append((min([r0] + [sp[0] for sp in over]), max([r1] + [sp[1] for sp in over])))
    spans.sort()

    info = []  # (start, end, normalised, token count)
    for s, e in spans:
        n = norm(text[s:e])
        if n:
            info.append((s, e, n, len(n.split())))

    present = set()  # full names that passed anywhere in this message
    out: dict[tuple[int, int], tuple[str, str]] = {}
    for s, e, n, k in info:
        if n in ignore:
            out[(s, e)] = ("pass", "IGNORED")
        elif k >= 2 and n in people.full:
            out[(s, e)] = ("pass", "FULL_" + people.full[n])
            present.add(people.canon.get(n, n))
        elif k >= 2:
            out[(s, e)] = ("name", "UNKNOWN_FULL")
    for s, e, n, k in info:
        if (s, e) in out:
            continue
        who = people.tokens.get(n, set())
        if len(who) == 1:
            pos = _position_ok(text, s, e)
            if pos:
                out[(s, e)] = ("pass", pos)
            elif next(iter(who)) in present:
                out[(s, e)] = ("pass", "BARE_WITH_FULL")
            else:
                out[(s, e)] = ("name", "BARE_MIDSENTENCE")
        elif len(who) > 1:
            out[(s, e)] = ("name", "AMBIGUOUS")
        else:
            out[(s, e)] = ("name", "UNKNOWN_BARE")

    numbers: dict[str, int] = {}
    decisions, counts, seen_review = [], Counter(), set()
    for s, e, n, k in info:
        action, reason = out[(s, e)]
        num = 0
        if action == "name":
            key = n
            if k == 1:  # reuse the number of an earlier name that contains this token
                key = next((m for m in numbers if n in m.split()), n)
            num = numbers.setdefault(key, len(numbers) + 1)
            if review is not None and reason.startswith("UNKNOWN") and n not in seen_review:
                seen_review.add(n)
                review[n] += 1
        decisions.append(Decision(s, e, action, reason, num))
        counts[reason] += 1
    return decisions, counts


# ---- local state (Z3): allowlist, ignore list, review counts ---------------------------------
def check_state_dir(path: Path) -> Path:
    """Refuse a state directory inside the repo or a OneDrive-synced folder (key-store rule)."""
    p = Path(path).expanduser().resolve()
    if REPO_ROOT == p or REPO_ROOT in p.parents:
        raise ValueError("state directory must not be inside the repo")
    if "onedrive" in str(p).lower():
        raise ValueError("state directory must not be inside OneDrive (Z3 is local and not synced)")
    return p


class State:
    """Local review state. With a protector (always, outside tests) it is stored encrypted.

    The plaintext `namegate-state.json` from slice 1 is migrated into the protected file on first
    open and then removed.
    """
    FILE = "namegate-state.json"       # legacy / test-only plaintext
    PFILE = "namegate-state.dpapi"

    def __init__(self, path: Path, protector=None):
        self.dir = check_state_dir(path)
        self.protector = protector
        self.file = self.dir / (self.PFILE if protector else self.FILE)
        self.data = {"staff": [], "external": [], "ignore": [], "counts": {}}
        legacy = self.dir / self.FILE
        if self.file.exists():
            self.data.update(self._read(self.file))
        elif protector and legacy.exists():
            self.data.update(json.loads(legacy.read_text(encoding="utf-8")))
            self.save()
            legacy.unlink()

    def _read(self, f: Path) -> dict:
        raw = f.read_bytes()
        return json.loads((self.protector.unprotect(raw) if self.protector else raw).decode("utf-8"))

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self.data, indent=1, sort_keys=True).encode("utf-8")
        tmp = self.file.with_suffix(".tmp")
        tmp.write_bytes(self.protector.protect(raw) if self.protector else raw)
        os.replace(tmp, self.file)

    def purge_counts(self) -> int:
        """Forget the unknown-name counts (they may include patients' names). Keeps the classifications."""
        n = len(self.data["counts"])
        self.data["counts"] = {}
        return n

    def people(self, base: People) -> People:
        out = base.copy()
        for n in self.data["staff"]:
            out.add(n, "LOCAL")
        return out

    def ignore(self) -> frozenset:
        return frozenset(self.data["ignore"])

    def merge_counts(self, c: Counter) -> None:
        for k, v in c.items():
            self.data["counts"][k] = self.data["counts"].get(k, 0) + v

    def classify(self, name: str, kind: str) -> str:
        n = norm(name)
        if kind not in ("staff", "external", "ignore") or not n:
            raise ValueError("kind must be staff, external or ignore, and the name must not be empty")
        for k in ("staff", "external", "ignore"):
            if n in self.data[k]:
                self.data[k].remove(n)
        self.data[kind].append(n)
        self.data["counts"].pop(n, None)
        return n
