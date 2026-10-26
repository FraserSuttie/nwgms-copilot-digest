"""Synthetic value generators for validator tests.

Nothing here is a real identifier, and the source tree holds no complete
identifier-shaped literal: values are assembled at runtime from parts.

- NHS-style numbers use a 999 prefix, which I understand is reserved for test
  use (not verified). The validator treats every checksum-valid number the same,
  whatever its range, so the choice only keeps fixtures clearly fictional.
- Phone numbers use the ranges Ofcom reserves for drama use.
- Name and domain values are invented (example.com is reserved for documentation).
"""

from __future__ import annotations

import re


def _check_digit(nine: str) -> int | None:
    total = sum(int(d) * w for d, w in zip(nine, range(10, 1, -1)))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return None if check == 10 else check


def nhs_digits(seed: int = 123456) -> str:
    """A 10-digit string with a valid modulus-11 check digit and a 999 prefix."""
    while True:
        nine = "999" + f"{seed:06d}"
        c = _check_digit(nine)
        if c is not None:
            return nine + str(c)
        seed += 1


def nhs_bad_digits() -> str:
    good = nhs_digits()
    return good[:9] + str((int(good[9]) + 1) % 10)


def _spaced(d: str) -> str:
    return " ".join((d[:3], d[3:6], d[6:]))


TOKENS = {
    "NHS_VALID": lambda: nhs_digits(),
    "NHS_SPACED": lambda: _spaced(nhs_digits(222222)),
    "NHS_BAD": lambda: nhs_bad_digits(),
    "PHONE_MOBILE": lambda: "0" + "7700" + " " + "900" + "123",
    "PHONE_LANDLINE": lambda: "01" + "632" + " " + "960" + "123",
    "PHONE_LONDON": lambda: "020" + " " + "7946" + " " + "0123",
    "PHONE_INTL": lambda: "+" + "44" + " " + "7700" + " " + "900123",
    "PHONE_COMPACT": lambda: "0" + "7700" + "900" + "123",
    "DOB_SLASH": lambda: "/".join(("12", "05", "1980")),
    "DOB_ISO": lambda: "-".join(("1980", "05", "12")),
    "DOB_TEXT": lambda: "3 " + "March" + " 1984",
    "DOB_US_TEXT": lambda: "March" + " 3, " + "1984",
    "POSTCODE": lambda: "ZZ1" + " " + "1ZZ",
    "EXT_EMAIL": lambda: "pat" + "@" + "example" + ".com",
    "SAMPLE_ID": lambda: "S" + "26" + "-" + "003M",
    "DERIVED_ID": lambda: "D" + "26" + "-" + "0A1Z",
    "REFERRAL_ID": lambda: "R" + "26" + "-" + "003M",
    "WORKSHEET_ID": lambda: "WS" + "26" + "-" + "00001",
    "QC_ID": lambda: "QC" + "26" + "-" + "0001",
    "CASE_ID": lambda: "2026" + "/" + "0001",
    "PATIENT_ID": lambda: "0" * 6 + "1",
    "PATIENT_ID_LARGE": lambda: "1234" + "567",
}


def fill(text: str) -> str:
    """Replace {{TOKEN}} placeholders with generated synthetic values."""
    def sub(m: re.Match) -> str:
        return TOKENS[m.group(1)]()
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", sub, text)
