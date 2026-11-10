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
    unknown_caps: int = 1         # DENS_CAPS when unknown capitalised words >= this: in any one field at 1, else the message total (ADR 0010)
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
            if h.code == "NER_PERSON" or (h.code == "NER_LOCATION" and G.loc_as_name(norm, h.start, h.end, people, ignore)):
                spans.append((h.start, h.end))   # ADR 0015: a staff or ignore-list word tagged as a place is gated as a name
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


# Public organisation words (ADR 0009): display names no longer carry them, so they are allowed here instead.
ORG_WORDS = frozenset("manchester university nhs foundation trust ft mft nwgms".split())


def allowed_words(people: G.People, ignore=frozenset(), vocab=frozenset(), header_people=()) -> frozenset:
    """Lower-case words that never count as 'unknown capitalised': staff tokens, config vocabulary,
    ignore-list terms (review queue), Trust-domain header participants, days and months."""
    words = set(DAYS_MONTHS) | ORG_WORDS | {_apos(w.lower()) for w in vocab}   # vocab entries with a space are phrases (ADR 0011)
    for tok in people.tokens:
        words.update(re.findall(r"[a-z0-9']+", _apos(tok)))
    for n in ignore:
        words.update(re.findall(r"[a-z0-9']+", _apos(n)))
    for display, addr in header_people:   # Trust-domain participants only: an external display name is not allowed
        dom = addr.rsplit("@", 1)[-1].lower()
        if any(dom == d or dom.endswith("." + d) for d in G.P.ALLOWED_EMAIL_DOMAINS):
            words.update(re.findall(r"[a-z0-9']+", _apos(display.lower())))
    return frozenset(words)


def _apos(s: str) -> str:
    return s.replace("\u2019", "'")


_APOS_BEFORE = re.compile(r"([A-Za-z]+)['\u2019]$")
_NEXT_WORD = re.compile(r" ([A-Za-z]+)")
_PREV_WORD = re.compile(r"([A-Za-z]+) $")


def _in_phrase(t: str, start: int, end: int, lw: str, allowed: frozenset) -> bool:
    """True when this word and its single-space neighbour form an allowed phrase, in that order (ADR 0011)."""
    nxt = _NEXT_WORD.match(t, end)
    if nxt and f"{lw} {nxt.group(1).lower()}" in allowed:
        return True
    prv = _PREV_WORD.search(t, max(0, start - 40), start)
    return bool(prv and f"{prv.group(1).lower()} {lw}" in allowed)


def load_phrases(path) -> frozenset:
    """Allowed multi-word phrases from a local text file, one per line (ADR 0011). Each is lower-cased with single
    spaces. A line with fewer than two words is refused, because a one-word phrase would just be vocabulary."""
    out = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        words = re.findall(r"[A-Za-z0-9'\u2019]+", line)
        if not words:
            continue
        if len(words) < 2:
            raise ValueError("each phrase needs two or more words")
        out.add(_apos(" ".join(words).lower()))
    return frozenset(out)


@dataclass(frozen=True)
class CapHit:
    """One counted unknown capitalised word. Offsets are into `blank_approved(text)`."""
    word: str
    start: int
    end: int
    initial: bool        # sentence-initial but counted (not followed by lower-case prose on the same line)


def unknown_cap_hits(text: str, allowed: frozenset) -> tuple[str, list[CapHit]]:
    """Capitalised words (3+ letters) that are not allowed, with positions. A sentence-initial word is skipped only
    when it is an ordinary opener and is followed by lower-case prose, so a bare name alone on a line still counts.
    Returns the blanked text the offsets refer to. This is the single definition of the DENS_CAPS count (ADR 0010)."""
    t = blank_approved(text)
    found = []
    phrases = any(" " in a for a in allowed)
    for m in _CAP.finditer(t):
        w = m.group(0)
        lw = w.lower()
        if lw in allowed or lw in OPENERS or any(p in allowed for p in lw.split("-")):
            continue
        ap = _APOS_BEFORE.search(t, max(0, m.start() - 40), m.start())
        if ap and f"{_apos(ap.group(1).lower())}'{lw}" in allowed:
            continue                # "O'Xxxx": the whole token is allowed (a Directory surname), not just its tail
        if phrases and _in_phrase(t, m.start(), m.end(), lw, allowed):
            continue
        pre = t[:m.start()].rstrip(" \t")
        initial = not pre or pre[-1] in ".!?:\n\r"
        if initial and t[m.end():m.end() + 24].lstrip(" \t")[:1].islower():
            continue            # ordinary sentence start: "Please check ..."
        found.append(CapHit(w, m.start(), m.end(), initial))
    return t, found


def unknown_cap_words(text: str, allowed: frozenset) -> list[str]:
    return [h.word for h in unknown_cap_hits(text, allowed)[1]]


def unknown_caps(text: str, allowed: frozenset) -> int:
    return len(unknown_cap_hits(text, allowed)[1])


# Shape counts for the caps diagnostic (ADR 0010). Counts only: no function here returns or logs a word.
PROFILE_KEYS = ("total", "initial", "mid", "hyphen", "possessive", "run2", "run3plus", "isolated", "alone_on_line",
                "titlecase_line")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")
_TITLE_TOK = re.compile(r"[A-Z][a-z]")
_SMALL = frozenset("a an and of the to in for on at by or with from as is are".split())


def _line_bounds(t: str, pos: int) -> tuple[int, int]:
    return t.rfind("\n", 0, pos) + 1, (t.find("\n", pos) if t.find("\n", pos) >= 0 else len(t))


def caps_profile(text: str, allowed: frozenset, part: str | None = None) -> Counter:
    """Shape of the unknown capitalised words in one field, as counts. Keys are PROFILE_KEYS.

    run2 / run3plus: the word sits in a run of 2 / 3+ adjacent capitalised words (allowed or not); isolated: it does not.
    alone_on_line: it is the only content on its line. titlecase_line: its line has 2+ words of 3+ letters and every
    such word starts with a capital (small words aside), as in a Title Case subject or a table header.
    `part` "subject" / "body" keeps only the hits before / after the first blank line of a `message` field."""
    t, hits = unknown_cap_hits(text, allowed)
    if part is not None:
        sep = t.find("\n\n")
        hits = [h for h in hits if (sep < 0 or h.start < sep) == (part == "subject")]
    c: Counter = Counter()
    for h in hits:
        c["total"] += 1
        c["initial" if h.initial else "mid"] += 1
        if "-" in h.word:
            c["hyphen"] += 1
        if t[h.end:h.end + 2] in ("'s", "\u2019s"):
            c["possessive"] += 1
        ls, le = _line_bounds(t, h.start)
        line = t[ls:le]
        toks = [(m.start() + ls, m.group(0)) for m in _TOKEN.finditer(line)]
        caps = [bool(_TITLE_TOK.match(w)) for _, w in toks]
        # the token holding the hit: a word after an apostrophe ("O'Xxxx") sits inside a longer token
        i = next((k for k, (pos, w) in enumerate(toks) if pos <= h.start < pos + len(w)), None)
        if i is None:
            c["isolated"] += 1
            continue
        a = b = i
        while a > 0 and caps[a - 1] and t[toks[a - 1][0] + len(toks[a - 1][1]):toks[a][0]] == " ":
            a -= 1
        while b + 1 < len(toks) and caps[b + 1] and t[toks[b][0] + len(toks[b][1]):toks[b + 1][0]] == " ":
            b += 1
        n = b - a + 1
        c["run3plus" if n >= 3 else "run2" if n == 2 else "isolated"] += 1
        if line.strip() == h.word:
            c["alone_on_line"] += 1
        big = [w for _, w in toks if len(w) >= 3 and w.lower() not in _SMALL]
        if len(big) >= 2 and all(_TITLE_TOK.match(w) for w in big):
            c["titlecase_line"] += 1
    return c



# ---- caps classifier (draft ADR 0017, diagnostic only) -------------------------------------------------------------
# Scores each unknown capitalised word on three checks so a held word can be told apart from a likely name:
#   position (3): SUBJECT (subject line) or TITLE_LINE (Title Case line) is neutral; PROSE is not.
#   shape (4):    TITLE (Mr/Dr... before), POSSESSIVE, PAIR (next to another unknown word or a [NAME-n]/[PT-...] token),
#                 ALONE (only word on its line, as in a sign-off), GREETING (after hi/dear/thanks...), HEADER
#                 (From/To/Cc field), NER (inside an NER person span).
#   nearby (5):   IDENT (an identifier token in the same sentence: REF/SMP/WS/QC/CASE, [PT-...], [GMC-ID], [CLIN], [CRED]).
# Tier: HOLD if any shape or nearby signal; else FLAG if the position is neutral; else PROSE. Signals and tiers are
# fixed strings: nothing here returns or logs a word. Offsets use a same-length blanking, so a hit set can differ from
# unknown_cap_hits in rare phrase edge cases (diagnostic only).
CLASS_SHAPE = ("TITLE", "POSSESSIVE", "PAIR", "ALONE", "GREETING", "HEADER", "NER")
CLASS_SIGNALS = ("SUBJECT", "TITLE_LINE", "PROSE") + CLASS_SHAPE + ("IDENT",)
CLASS_TIERS = ("FLAG", "PROSE", "HOLD")
_IDENT_TOKEN = re.compile(r"^(?:(?:REF|SMP|WS|QC|CASE)-|\[(?:PT-|GMC-ID|CLIN|CRED))")
_PAIR_TOKEN = re.compile(r"^\[(?:NAME-\d+|PT-[A-Z]+)\]$")
_TITLES = frozenset("mr mrs ms miss mx dr prof professor sir dame".split())
_GREET = re.compile(r"(?:^|[\s,])(?:hi|hello|hey|dear|morning|afternoon|evening|thanks|thank you|cheers|regards|wishes|ta)"
                    r"[\s,!.]*$", re.I)


def _same_length_blank(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    tokens = [(m.start(), m.end(), m.group(0)) for m in APPROVED_RE.finditer(text)]
    return APPROVED_RE.sub(lambda m: " " * len(m.group(0)), text), tokens


def classify_caps(text: str, allowed: frozenset, field_name: str = "message", ner_spans=None) -> list[tuple[str, frozenset]]:
    """(tier, signals) for each unknown capitalised word in one output field. `ner_spans`: (start, end) person spans
    over the same-length blanked text, or None when NER is off."""
    t, tokens = _same_length_blank(text)
    sub_end = t.find("\n\n") if field_name == "message" else -1
    header = field_name in ("header_from", "header_to", "header_cc")
    hits = []
    for m in _CAP.finditer(t):
        w, lw = m.group(0), m.group(0).lower()
        if lw in allowed or lw in OPENERS or any(x in allowed for x in lw.split("-")):
            continue
        ap = _APOS_BEFORE.search(t, max(0, m.start() - 40), m.start())
        if ap and f"{_apos(ap.group(1).lower())}'{lw}" in allowed:
            continue
        if any(" " in a for a in allowed) and _in_phrase(t, m.start(), m.end(), lw, allowed):
            continue
        pre = t[:m.start()].rstrip(" \t")
        if (not pre or pre[-1] in ".!?:\n\r") and t[m.end():m.end() + 24].lstrip(" \t")[:1].islower():
            continue
        hits.append((m.start(), m.end()))
    starts = {s for s, _ in hits}
    ends = {e for _, e in hits}
    out = []
    for s, e in hits:
        sig = set()
        ls, le = _line_bounds(t, s)
        line = text[ls:le]
        toks = [w for w in _TOKEN.findall(t[ls:le])]
        big = [w for w in toks if len(w) >= 3 and w.lower() not in _SMALL]
        title_line = len(big) >= 2 and all(_TITLE_TOK.match(w) for w in big)
        if sub_end >= 0 and s < sub_end:
            sig.add("SUBJECT")
        elif title_line:
            sig.add("TITLE_LINE")
        else:
            sig.add("PROSE")
        if header:
            sig.add("HEADER")
        before = t[max(ls, s - 12):s]
        bm = re.search(r"([A-Za-z]+)\.?\s+$", before)
        if bm and bm.group(1).lower() in _TITLES:
            sig.add("TITLE")
        if t[e:e + 2] in ("'s", "’s"):
            sig.add("POSSESSIVE")
        left, right = text[ls:s].rstrip(" "), text[e:le].lstrip(" ")
        if not title_line and ((s - 1 in ends and t[s - 1:s] == " ") or (e + 1 in starts and t[e:e + 1] == " ") \
                or (left and _PAIR_TOKEN.match(left.split(" ")[-1] or "")) or (right and _PAIR_TOKEN.match(right.split(" ")[0] or ""))):
            sig.add("PAIR")   # not on a Title Case line, where every word is capitalised anyway
        if line.strip(" \t,.!") == t[s:e]:
            sig.add("ALONE")
        if _GREET.search(t[ls:s]):
            sig.add("GREETING")
        if ner_spans and any(a < e and s < b for a, b in ner_spans):
            sig.add("NER")
        a0 = max(max(t.rfind(c, 0, s) for c in ".!?") + 1, t.rfind("\n\n", 0, s) + 2, s - 200, 0)   # same sentence,
        b0 = min([x for x in (t.find(c, e) for c in ".!?") if x >= 0]                              # 200 chars at most
                 + [x for x in (t.find("\n\n", e),) if x >= 0] + [e + 200, len(t)])
        if any(a0 <= ts < b0 and _IDENT_TOKEN.match(tok) for ts, _, tok in tokens):
            sig.add("IDENT")
        tier = "HOLD" if sig & (set(CLASS_SHAPE) | {"IDENT"}) else "FLAG" if sig & {"SUBJECT", "TITLE_LINE"} else "PROSE"
        out.append((tier, frozenset(sig)))
    return out


def caps_policy(fields: dict, allowed: frozenset, use_ner: bool = False) -> dict:
    """Message-level summary over the non-attachment output fields: tier and signal counts, and whether each draft policy
    would release it (when DENS_CAPS is its only reason). A: every word is FLAG. B: no word is HOLD. Counts only."""
    tiers, signals, hold_signals, n = Counter(), Counter(), Counter(), 0
    for name, text in fields.items():
        if name.startswith("attachment_"):
            continue
        spans = None
        if use_ner:
            t, _ = _same_length_blank(text)
            spans = [(h.start, h.end) for h in N.detect_ner(t) if h.code == "NER_PERSON"]
        for tier, sig in classify_caps(text, allowed, name, spans):
            n += 1
            tiers[tier] += 1
            signals.update(sig)
            if tier == "HOLD":
                hold_signals.update(sig & (set(CLASS_SHAPE) | {"IDENT"}))
    return {"words": n, "tiers": tiers, "signals": signals, "hold_signals": hold_signals,
            "release_a": n > 0 and tiers["FLAG"] == n, "release_b": n > 0 and tiers["HOLD"] == 0}

def density(text: str, allowed: frozenset, limits: Limits = Limits(), caps: bool = True) -> Counter:
    """`caps=False` leaves DENS_CAPS to the caller (the pipeline counts the message total when the limit is above 1)."""
    r = Counter()
    if caps and unknown_caps(text, allowed) >= limits.unknown_caps:
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


# ---- cue check (ADR 0008): a word straight after a patient cue must be recognised ---------------------------------
CUES = frozenset("patient pt mr mrs ms miss mx baby proband".split())
# General English words that commonly follow a cue. Written from general usage, not from the corpus. Anything else
# within the window (staff, config vocabulary and ignore-list words aside) quarantines the whole message (CUE_NAME).
CUE_FOLLOW = frozenset("""a an the this that these those his her their its our your my one two both each every all any some
no not and or but nor so if as at by for from in into of on onto to with without about after before since until
is are was were be been being has have had having do does did will would can could should shall may might must
who whom whose which what when where why how here there then now also still already just only again ever never
yet too very more most less least well back up down out off over under same other another such own
seen saw see sees seeing told says said say called calls rang rung came come comes went go goes going gone
seems seemed appears looked looks needs needed wants wanted asked asks gave given got gets made make makes
isn't wasn't hasn't hadn't doesn't didn't won't can't couldn't wouldn't
sample samples referral referrals record records details detail notes note file files list name names number
numbers id identifier results result report reports request requests test tests testing tested form forms data
consent consents letter letters information info query queries case cases episode episodes visit visits
appointment appointments clinic clinics ward wards team teams care family families group groups cohort
feedback survey surveys safety pathway pathways portal journey experience experiences records
level levels list lists side group groups
admin administration access transport parking leaflet leaflets
due booked received pending waiting awaiting arrived attended discharged admitted transferred referred
registered logged booked cancelled moved updated added removed linked merged unlinked processed
""".split())
_CUE_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*|\[[A-Z0-9-]+\]|\n|[^\sA-Za-z]")
_CUE_STOP = frozenset(".!?;:\n()\"")
CUE_WINDOW = 2
_CUE_ABBR = frozenset("pt mr mrs ms mx".split())


def cue_check(text: str, allowed: frozenset, window: int = CUE_WINDOW) -> Counter:
    """CUE_NAME when a word in the `window` words after a cue is not recognised. Fail-closed. Counts only.

    Recognised: approved tokens and placeholders, words with digits, CUE_FOLLOW, `allowed` (staff tokens, config
    vocabulary, ignore list, Trust-domain header names, days and months). The window stops at sentence
    punctuation, a bracket or a newline (a colon or dash straight
    after the cue is a label and is skipped). Catches lower-case names after a cue ("seen pt jane doe"); it does
    NOT catch a name with no cue next to it."""
    r = Counter()
    toks = [m.group(0) for m in _CUE_WORD.finditer(APPROVED_RE.sub(" [TOKEN] ", text))]
    for i, t in enumerate(toks):
        if t.lower().rstrip(".") not in CUES:
            continue
        j, seen = i + 1, 0
        if j < len(toks) and toks[j] == "." and t.lower() in _CUE_ABBR:   # "pt." / "Mr.": abbreviation, not a sentence end
            j += 1
        if j < len(toks) and toks[j] in (":", "-"):    # label form: "Patient: ..." / "pt - ..."
            j += 1
        while j < len(toks) and seen < window:
            w = toks[j]
            if w in _CUE_STOP or (len(w) == 1 and not w.isalpha()):
                break
            if w.startswith("["):
                break                                  # a placeholder or token ends the window: already treated
            base = w.lower().replace("\u2019", "'")
            base = base[:-2] if base.endswith("'s") else base
            if not (base in CUE_FOLLOW or base in allowed or all(p in allowed or p in CUE_FOLLOW for p in base.split("-"))):
                r["CUE_NAME"] += 1
                break
            seen += 1
            j += 1
    return r


def load_wordlist(path) -> frozenset:
    """Config vocabulary from a local text file, one term per line (test, panel and gene names from the NGTD
    reference). The file lives outside the repo; the pipeline only ever lower-cases and splits it into words."""
    words = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        words.update(re.findall(r"[A-Za-z0-9']+", line.strip()))
    return frozenset(words)
