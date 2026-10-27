"""Deterministic detectors for the content-lane stripper.

Reuses validator/patterns.py for NHS checksum, phone, NHS, date, postcode, external email and
iGene ID patterns, and adds the detectors the stripper needs: loose NHS grouping, digits
written as words, MRN, GMC-near-digits, street addresses, personal URLs, DOB cue without a
year, and a placeholder clinical lexicon. Findings carry code, severity and position only; no
matched text is ever returned or logged.

The text is NFKC-normalised first, so spans refer to the normalised string. Callers must
transform that string, not the original.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator"))
import patterns as P  # noqa: E402

Hit = P.Hit

_SEP = r"[ \-./\n]{0,2}"
_NHS_LOOSE = re.compile(rf"(?<![\d])(?:\d{_SEP}){{9}}\d(?!\d)")
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_WORDS_RUN = re.compile(rf"\b{_DIGIT_WORD}(?:[\s,\-]+{_DIGIT_WORD}\b){{6,}}", re.I)
_MRN_ODS7 = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{3}[ \-/]?\d{7}(?!\d)")
_MRN_8 = re.compile(r"(?<!\d)\d{8}(?!\d)")
_GMC = re.compile(r"\bGMC\b[^\d\n]{0,20}(\d{7})(?!\d)", re.I)
_PHONE_BRACKET = re.compile(r"(?<!\d)\(0\d{2,4}\)[ \-.]?\d{3,4}[ \-.]?\d{3,4}(?!\d)")
_STREET = re.compile(
    r"(?:\b(?:Flat|Apt|Apartment|Unit)\s+\w+,?\s+)?\b\d{1,4}[A-Za-z]?\s+[A-Z][A-Za-z']+(?:\s+[A-Z][A-Za-z']+){0,2}\s+"
    r"(?:Road|Rd|Street|St|Lane|Ln|Avenue|Ave|Close|Drive|Dr|Way|Court|Gardens|Crescent|Place|Terrace|Grove|Mews|Row)\b")
_URL = re.compile(r"https?://[^\s<>\"')]+", re.I)
_URL_PERSONAL = re.compile(r"/personal/([A-Za-z0-9_\-.]+)", re.I)
_DOB_PARTIAL = re.compile(
    rf"(?:\bdob\b|\bd\.o\.b\.?|date\s+of\s+birth|\bborn\b)[:\s]*(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{P._MONTH}\b)(?!\s*,?\s*\d{{2,4}}\b)", re.I)

# Placeholder clinical lexicon (word level). To be replaced by a file derived from the NGTD
# reference; patient-level wording only, config vocabulary is never matched here.
_VARIANT = re.compile(r"(?<![\w.])(?:[cgnm]\.[\d_*+\-]+[ACGT]?(?:>[ACGT]|del|dup|ins)[A-Za-z0-9]*|p\.\(?[A-Z][a-z]{2}\d+[A-Za-z]{2,3}\)?)")
_RESULT = re.compile(r"\b(?:not detected|likely pathogenic|pathogenic|detected|carrier|positive|negative|VUS)\b", re.I)
_AGE = re.compile(r"\b(?:\d{1,3}[- ]?(?:year|yr)s?[- ]old|aged?\s+\d{1,3}|\d{1,3}\s?(?:y/?o|yo))\b", re.I)
_DX_TERMS = ("breast cancer", "ovarian cancer", "acute myeloid leukaemia", "leukaemia", "lymphoma", "cystic fibrosis",
             "lynch syndrome", "polycythaemia vera", "huntington")
_DX = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in _DX_TERMS) + r")\b", re.I)

_WORD_DIGIT = {w: str(i) for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"])}
_WORD_DIGIT["oh"] = "0"


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _claim(hits: list[Hit], new: Hit) -> bool:
    for h in hits:
        if new.start < h.end and h.start < new.end:
            return False
    hits.append(new)
    return True


def detect(text: str) -> tuple[str, list[Hit]]:
    """Return (normalised_text, hits sorted by start). Specific detectors claim spans first."""
    text = normalise(text)
    raw = text
    text = raw.replace("_", " ")  # same length; "_" is a word char and would defeat \b in filenames
    hits: list[Hit] = []

    # Phones claim first: a 10-digit run after +44 is a phone number, not an NHS number.
    for h in P.scan_text(text):
        if h.code == "PHONE_UK":
            _claim(hits, h)
    for m in _PHONE_BRACKET.finditer(text):
        _claim(hits, Hit("PHONE_UK", "fail", m.start(), m.end()))

    for m in _GMC.finditer(text):
        _claim(hits, Hit("GMC_ID", "fail", m.start(1), m.end(1)))

    for m in _WORDS_RUN.finditer(text):
        words = re.findall(_DIGIT_WORD, m.group(0), re.I)
        digits = "".join(_WORD_DIGIT[w.lower()] for w in words)
        code = "NHS_NUMBER" if len(digits) == 10 and P.nhs_checksum_ok(digits) else "DIGITS_AS_WORDS"
        _claim(hits, Hit(code, "fail", m.start(), m.end()))

    for m in _MRN_ODS7.finditer(text):
        _claim(hits, Hit("MRN", "fail", m.start(), m.end()))

    for m in _NHS_LOOSE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        code = "NHS_NUMBER" if P.nhs_checksum_ok(digits) else "TEN_DIGITS"  # near-miss still fails
        _claim(hits, Hit(code, "fail", m.start(), m.end()))

    for m in _STREET.finditer(text):
        _claim(hits, Hit("ADDRESS_STREET", "fail", m.start(), m.end()))

    for m in _URL.finditer(raw):
        pm = _URL_PERSONAL.search(m.group(0))
        if pm:
            _claim(hits, Hit("URL_PERSONAL", "fail", m.start() + pm.start(1), m.start() + pm.end(1)))
        else:
            host = re.sub(r"^https?://", "", m.group(0), flags=re.I).split("/")[0].lower()
            if not any(host == d or host.endswith("." + d) for d in P.ALLOWED_EMAIL_DOMAINS):
                _claim(hits, Hit("URL_EXTERNAL", "review", m.start(), m.end()))

    for m in _DOB_PARTIAL.finditer(text):
        _claim(hits, Hit("DOB_PARTIAL", "fail", m.start(1), m.end(1)))

    for m in _MRN_8.finditer(text):
        _claim(hits, Hit("MRN", "fail", m.start(), m.end()))

    for rx, code in ((_VARIANT, "CLIN_VARIANT"), (_RESULT, "CLIN_RESULT"), (_AGE, "CLIN_AGE"), (_DX, "CLIN_DX")):
        for m in rx.finditer(raw if rx is _VARIANT else text):
            _claim(hits, Hit(code, "fail", m.start(), m.end()))

    for h in P.scan_text(text):
        _claim(hits, h)

    return raw, sorted(hits, key=lambda h: h.start)
