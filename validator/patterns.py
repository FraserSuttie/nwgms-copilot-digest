"""Pattern library for the digest validator (standard library only).

Everything here is deterministic. No LLM is involved in any decision.

Design notes
- Detectors return findings by code and position only. Callers never echo the
  matched text, so a leaked value is not copied into logs or reports.
- Findings have a severity: "fail" (certain or near-certain leak) or "review"
  (ambiguous, kept so patterns can be tuned on evidence). In Phase 1 both block
  a file; --lenient on the CLI lets "review" findings through.
- Specific detectors run first. A later detector cannot report a span that an
  earlier one already covers, so each leaked value gives one finding.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ----------------------------------------------------------------------------
# Allowlists (spec section 5)
# ----------------------------------------------------------------------------
SOURCES = ("email", "teams-chat", "teams-channel", "meeting-recap")
SYSTEMS = (
    "iGene", "Clarity", "DLIMS", "StarLIMS", "Congenica",
    "Hive/Epic", "TIE", "Monday.com", "Other",
)
REQUEST_TYPES = (
    "access request", "config change", "incident/outage", "testing/UAT",
    "report/query", "scheduling", "governance/SOP", "FYI",
)
ACTIONS = ("reply", "review", "approve", "attend", "none")
DIRECTIONS = ("owed-by-me", "awaited-from-them", "fyi")

# lane -> (allowed sources, allowed directions)
LANES = {
    "A": (("email",), ("owed-by-me",)),
    "B1": (("teams-chat", "teams-channel"), DIRECTIONS),
    "B4": (("meeting-recap",), DIRECTIONS),
}

ALLOWED_EMAIL_DOMAINS = ("nhs.net", "mft.nhs.uk")

# Allowed shapes for the `refs` field (spec section 7). Anything else is rejected.
REF_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"CRF-\d{4}-\d{3}",   # change request
        r"#\d{4,6}",          # Genial ticket when first raised
        r"IG-\d{4,6}",        # Jira ticket once Genial adopts it
        r"DOC\d{4,5}",        # Q-Pulse document
    )
)

RECEIVED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?")
DUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_allowed_ref(value: str) -> bool:
    return any(p.fullmatch(value) for p in REF_PATTERNS)


# ----------------------------------------------------------------------------
# NHS number
# ----------------------------------------------------------------------------
def nhs_checksum_ok(digits: str) -> bool:
    """Modulus 11 check for a 10-digit NHS number."""
    if len(digits) != 10 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    if check == 10:
        return False
    return check == int(digits[9])


# ----------------------------------------------------------------------------
# Detectors
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Hit:
    code: str
    severity: str  # "fail" or "review"
    start: int
    end: int


_EMAIL_RE = re.compile(r"[\w.+\-]+@([\w\-]+(?:\.[\w\-]+)+)")

# UK phone numbers: national form (leading 0, 10 or 11 digits) and +44 / 0044.
_SEP = r"[ \-.]?"
_PHONE_NATIONAL_RE = re.compile(rf"(?<!\d)0(?:{_SEP}\d){{9,10}}(?!\d)")
_PHONE_INTL_RE = re.compile(rf"(?<!\d)(?:\+|00){_SEP}44(?:{_SEP}\(?0\)?)?(?:{_SEP}\d){{9,10}}(?!\d)")

_NHS_RE = re.compile(rf"(?<!\d)\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{4}}(?!\d)")

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE_PATTERNS = (
    # (code, severity, regex)
    ("DATE", "fail", re.compile(r"(?<!\d)\d{1,2}[/\-]\d{1,2}[/\-](?:\d{4}|\d{2})(?!\d)")),
    ("DATE", "fail", re.compile(r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{4}(?!\d)")),
    ("DATE", "fail", re.compile(r"(?<!\d)\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}(?!\d)")),
    ("DATE", "fail", re.compile(rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\.?,?\s+\d{{2,4}}(?!\d)", re.I)),
    ("DATE", "fail", re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}(?!\d)", re.I)),
    # dotted two-digit year looks like a version number too, so only review it
    ("DATE_DOTTED", "review", re.compile(r"(?<![\d.])\d{1,2}\.\d{1,2}\.\d{2}(?![\d.])")),
    ("MONTH_YEAR", "review", re.compile(rf"\b{_MONTH}\.?\s+(?:19|20)\d{{2}}(?!\d)", re.I)),
)
_DOB_KEYWORD_RE = re.compile(r"\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|born)\b", re.I)

_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)

# iGene identifiers (spec section 7). Base-34 = digits and letters except I and O.
_B34 = "0-9A-HJ-NP-Z"
_IGENE_PATTERNS = (
    ("IGENE_SAMPLE", "fail", re.compile(rf"\b[SD]\d{{2}}-[{_B34}]{{4}}\b", re.I)),
    ("IGENE_REFERRAL", "fail", re.compile(rf"\bR\d{{2}}-[{_B34}]{{4}}\b", re.I)),
    ("IGENE_ID_WORKSHEET", "fail", re.compile(r"\bWS\d{2}-\d{5}\b", re.I)),
    ("IGENE_ID_QC", "fail", re.compile(r"\bQC\d{2}-\d{4}\b", re.I)),
    ("IGENE_ID_PREFIXED", "fail", re.compile(r"\b[A-Z]{1,2}\d{2}-[0-9A-Z]{3,5}(?:-[0-9A-Z])?\b", re.I)),
    ("IGENE_CASE", "fail", re.compile(r"(?<!\d)\d{4}/\d{4}(?!\d)")),
    ("PATIENT_NUMBER", "fail", re.compile(r"(?<!\d)\d{7}(?!\d)")),
    ("SIX_DIGITS", "review", re.compile(r"(?<!\d)\d{6}(?!\d)")),
    # any other long digit run is unexplained, so hold it for review
    ("LONG_DIGITS", "review", re.compile(r"(?<!\d)\d{8,}(?!\d)")),
)


def _claim(hits: list[Hit], new: Hit) -> None:
    """Add a hit unless it overlaps one that is already recorded."""
    for h in hits:
        if new.start < h.end and h.start < new.end:
            return
    hits.append(new)


def scan_text(text: str) -> list[Hit]:
    """Scan one string and return findings. Never returns the matched text."""
    text = unicodedata.normalize("NFKC", text)
    hits: list[Hit] = []

    for m in _EMAIL_RE.finditer(text):
        domain = m.group(1).lower()
        if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
            _claim(hits, Hit("EMAIL_EXTERNAL", "fail", m.start(), m.end()))

    for rx in (_PHONE_INTL_RE, _PHONE_NATIONAL_RE):
        for m in rx.finditer(text):
            _claim(hits, Hit("PHONE_UK", "fail", m.start(), m.end()))

    for m in _NHS_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if nhs_checksum_ok(digits):
            _claim(hits, Hit("NHS_NUMBER", "fail", m.start(), m.end()))
        else:
            _claim(hits, Hit("TEN_DIGITS", "review", m.start(), m.end()))

    for code, sev, rx in _DATE_PATTERNS:
        for m in rx.finditer(text):
            _claim(hits, Hit(code, sev, m.start(), m.end()))
    for m in _DOB_KEYWORD_RE.finditer(text):
        _claim(hits, Hit("DOB_KEYWORD", "review", m.start(), m.end()))

    for m in _POSTCODE_RE.finditer(text):
        _claim(hits, Hit("POSTCODE", "fail", m.start(), m.end()))

    for code, sev, rx in _IGENE_PATTERNS:
        for m in rx.finditer(text):
            _claim(hits, Hit(code, sev, m.start(), m.end()))

    return sorted(hits, key=lambda h: h.start)
