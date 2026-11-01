"""Residual scan and density check (Comms-Intake-Design section 5 step 5, ADR 0005).

Fail-closed: every function returns a Counter of REASON CODES (counts only, never values). An empty
Counter means the text passed; anything else sends the WHOLE message to quarantine.

residual scan   Re-runs every detector (and, with NER on, the NER layer and the name gate) over the
                output. Approved token formats are blanked first, but each approved-format string
                must also be one the transformer actually emitted for this message, so token-shaped
                text that arrived in the raw input does not pass. DOB_KEYWORD is allowed (decision of
                27 Oct 2026); every other hit, including review-severity codes, fails.
density check   The only net for names the NER layer misses: capitalised words that are not known
                staff, config vocabulary, days/months or header participants; long digit runs; and
                [CLIN] markers. Thresholds live in `Limits` and are measured by density.py.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detectors as D  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
from transform import APPROVED_RE, blank_approved  # noqa: E402

ALLOWED_CODES = frozenset({"DOB_KEYWORD"})
ATT_LINE_RE = re.compile(r'ATT-\d+ \([a-z0-9]{1,5}(?:, "[^"\n]*")?\)')

DAYS_MONTHS = frozenset("""monday tuesday wednesday thursday friday saturday sunday january february march april may june july
august september october november december""".split())
OPENERS = frozenset("""hi hello dear thanks thank regards cheers best kind many yours sincerely apologies sorry please yes no
also however but and so if when then note ok okay fyi the this that these those there here can could would will should shall
may might our your their his her its we they you who what why how which not just now any all some let see for from with
without about after before over under into onto are was were did does has have had been being is it""".split())
_CAP = re.compile(r"(?<![\w\[-])[A-Z][a-z]{2,}(?:-[A-Z][a-z]+)?\b")
_DIGITS = re.compile(r"\d")


@dataclass(frozen=True)
class Limits:
    max_chars: int = 20000        # ELIG_SIZE: total characters in the non-attachment fields
    unknown_caps: int = 1         # DENS_CAPS when the count of unknown capitalised words is >= this
    digit_run: int = 5            # DENS_DIGITS: any run of this many digits or more
    clin_markers: int = 3         # DENS_CLIN when [CLIN] occurs this many times or more


def scan_attachment(line: str, emitted) -> Counter:
    r = Counter()
    if not ATT_LINE_RE.fullmatch(line) or line not in emitted:
        r["ATT_LINE_INVALID"] += 1
    return r


def scan(text: str, emitted, people: G.People, ignore=frozenset(), use_ner: bool = False,
         allowed: frozenset = frozenset()) -> Counter:
    """Residual scan over one output field. `allowed` (lower-case words, as in density) exempts NER place hits on
    known domain words such as a leftover "QC"."""
    r = Counter()
    for m in APPROVED_RE.finditer(text):
        if m.group(0) not in emitted:
            r["TOKEN_NOT_ISSUED"] += 1
    blank = blank_approved(text)
    norm, hits = D.detect(blank)
    spans = []
    if use_ner:
        for h in N.detect_ner(norm):
            if h.code == "NER_PERSON":
                spans.append((h.start, h.end))
            elif norm[h.start:h.end].lower() in allowed:
                continue
            elif not any(h.start < x.end and x.start < h.end for x in hits):
                hits.append(h)
    for h in hits:
        if h.code not in ALLOWED_CODES:
            r[f"HIT_{h.code}"] += 1
    decisions, _ = G.gate(norm, spans, people, ignore=ignore)
    n = sum(1 for d in decisions if d.action == "name")
    if n:
        r["NAME_SURVIVES"] += n
    return r


def allowed_words(people: G.People, ignore=frozenset(), vocab=frozenset(), header_people=()) -> frozenset:
    """Lower-case words that never count as 'unknown capitalised': staff tokens, config vocabulary,
    ignore-list terms (review queue), Trust-domain header participants, days and months."""
    words = set(DAYS_MONTHS) | {w.lower() for w in vocab}
    for tok in people.tokens:
        words.update(re.findall(r"[a-z0-9']+", tok))
    for n in ignore:
        words.update(re.findall(r"[a-z0-9']+", n))
    for display, addr in header_people:   # Trust-domain participants only: an external display name is not allowed
        dom = addr.rsplit("@", 1)[-1].lower()
        if any(dom == d or dom.endswith("." + d) for d in G.P.ALLOWED_EMAIL_DOMAINS):
            words.update(re.findall(r"[a-z0-9']+", display.lower()))
    return frozenset(words)


def unknown_cap_words(text: str, allowed: frozenset) -> list[str]:
    """Capitalised words (3+ letters) that are not allowed. A sentence-initial word is skipped only when
    it is an ordinary opener and is followed by lower-case prose, so a bare name alone on a line still counts."""
    t = blank_approved(text)
    found = []
    for m in _CAP.finditer(t):
        w = m.group(0)
        lw = w.lower()
        if lw in allowed or lw in OPENERS or any(p in allowed for p in lw.split("-")):
            continue
        pre = t[:m.start()].rstrip(" \t")
        initial = not pre or pre[-1] in ".!?:\n\r"
        if initial and t[m.end():m.end() + 24].lstrip(" \t")[:1].islower():
            continue            # ordinary sentence start: "Please check ..."
        found.append(w)
    return found


def unknown_caps(text: str, allowed: frozenset) -> int:
    return len(unknown_cap_words(text, allowed))


def density(text: str, allowed: frozenset, limits: Limits = Limits()) -> Counter:
    r = Counter()
    if unknown_caps(text, allowed) >= limits.unknown_caps:
        r["DENS_CAPS"] += 1
    run, best = 0, 0
    for ch in blank_approved(text):
        run = run + 1 if ch.isdigit() else 0
        best = max(best, run)
    if best >= limits.digit_run:
        r["DENS_DIGITS"] += 1
    if text.count("[CLIN]") >= limits.clin_markers:
        r["DENS_CLIN"] += 1
    return r


def load_wordlist(path) -> frozenset:
    """Config vocabulary from a local text file, one term per line (test, panel and gene names from the NGTD
    reference). The file lives outside the repo; the pipeline only ever lower-cases and splits it into words."""
    words = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        words.update(re.findall(r"[A-Za-z0-9']+", line.strip()))
    return frozenset(words)
