"""Tests for the stripper detectors, NER stub and recall report. Run from the repo root:

    python -m unittest discover -s stripper/tests -v

All values are synthetic and assembled from parts (see validator/tests/generators.py).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator", "validator/tests"):
    sys.path.insert(0, str(ROOT / sub))

import detectors as D  # noqa: E402
import generate as C  # noqa: E402
import generators as VG  # noqa: E402


def codes(text: str):
    t, hits = D.detect(text)
    return [(h.code, t[h.start:h.end]) for h in hits]


def covers(text: str, value: str) -> bool:
    t, hits = D.detect(text)
    i = t.index(value)
    return all(any(h.start <= j < h.end for h in hits) for j in range(i, i + len(value)) if t[j].isalnum())


class NhsTests(unittest.TestCase):
    d = VG.nhs_digits(314159)

    def test_plain_spaced_hyphen_odd_groups(self):
        for v in (self.d, f"{self.d[:3]} {self.d[3:6]} {self.d[6:]}", f"{self.d[:3]}-{self.d[3:6]}-{self.d[6:]}",
                  f"{self.d[:2]} {self.d[2:7]} {self.d[7:]}"):
            self.assertTrue(covers(f"NHS {v} ok", v), v)

    def test_split_over_newline(self):
        v = f"{self.d[:3]} {self.d[3:6]}\n{self.d[6:]}"
        self.assertTrue(covers(f"NHS {v} ok", v))

    def test_digits_as_words(self):
        w = " ".join(C.DIGIT_WORDS[int(c)] for c in self.d)
        self.assertTrue(covers(f"it is {w}.", w))

    def test_near_miss_still_fails(self):
        bad = self.d[:9] + str((int(self.d[9]) + 1) % 10)
        self.assertEqual(codes(f"x {bad} y")[0][0], "TEN_DIGITS")

    def test_fullwidth_digits(self):
        fw = "".join(chr(0xFF10 + int(c)) for c in self.d)
        _, hits = D.detect(f"NHS {fw}")
        self.assertEqual([h.code for h in hits], ["NHS_NUMBER"])

    def test_intl_phone_not_swallowed_as_nhs(self):
        v = "+44 " + "7700 900" + "123"
        self.assertEqual([c for c, _ in codes(f"call {v}")], ["PHONE_UK"])
        self.assertTrue(covers(f"call {v}", v))


class IdentifierTests(unittest.TestCase):
    def test_igene_ids_in_filename_with_underscores(self):
        ref = "R" + "26" + "-" + "003M"
        s = f"Report_Someone_{ref}.pdf"
        self.assertTrue(covers(s, ref))

    def test_worksheet_qc_case(self):
        for v in ("WS" + "26" + "-" + "00001", "QC" + "26" + "-" + "0001", "2026" + "/" + "0001"):
            self.assertTrue(covers(f"see {v} now", v), v)

    def test_mrn_shapes_and_separator(self):
        digits = "1234567"
        for v in ("XQA" + digits, "XQA" + "-" + digits, "xqa" + digits, "12345678"):
            self.assertTrue(covers(f"MRN {v} here", v), v)

    def test_gmc_only_digits_labelled_as_gmc(self):
        self.assertEqual(codes("GMC no. " + "1234567" + " ok"), [("GMC_ID", "1234567")])

    def test_bracket_phone(self):
        v = "(0161) 496 " + "0123"
        self.assertTrue(covers(f"tel {v}", v))

    def test_street_address_and_postcode(self):
        s = "Flat 4, 22 Brackenmoor Lane, ZZ1 1ZZ"
        self.assertTrue(covers(s, "Flat 4, 22 Brackenmoor Lane"))
        self.assertTrue(covers(s, "ZZ1 1ZZ"))

    def test_dob_with_and_without_year(self):
        self.assertEqual(codes("DOB 3 March 1984")[-1][1], "3 March 1984")
        self.assertTrue(covers("born 3rd March", "3rd March"))

    def test_personal_url_flagged_trust_url_not(self):
        bad = "https://x-my.sharepoint.example.com/personal/a_b_mft_nhs_uk/Documents/f.docx"
        self.assertIn("URL_PERSONAL", [c for c, _ in codes(bad)])
        ok = "https://intranet.mft.nhs.uk/news/release"
        self.assertEqual(codes(ok), [])
        self.assertEqual([c for c, _ in codes("https://example.com/page")], ["URL_EXTERNAL"])

    def test_external_email_flagged_trust_email_not(self):
        self.assertEqual([c for c, _ in codes("a.b" + "@" + "example.com")], ["EMAIL_EXTERNAL"])
        self.assertEqual(codes("a.b" + "@" + "mft.nhs.uk"), [])


class ClinicalTests(unittest.TestCase):
    def test_patient_level_wording_hit(self):
        for v in ("c.1234A>G", "p.Arg34Ter", "54-year-old", "aged 54", "breast cancer", "not detected"):
            self.assertTrue(covers(f"note: {v}.", v), v)

    def test_config_vocabulary_untouched(self):
        for v in ("BRCA1", "TP53", "Cancer Panel R208", "CRF-2026-019", "DOC6676"):
            self.assertEqual(codes(f"The {v} config change"), [], v)

    def test_known_false_strip_is_documented(self):
        # Word-level result vocabulary also fires on benign prose. Tracked in the recall report
        # as an unlabelled CLIN_RESULT count; the lexicon slice will tune it.
        self.assertEqual([c for c, _ in codes("Thanks for the positive feedback")], ["CLIN_RESULT"])

    def test_every_date_is_flagged_known_utility_cost(self):
        self.assertEqual([c for c, _ in codes("Meeting on 30 October 2026")], ["DATE"])


class HitsCarryNoText(unittest.TestCase):
    def test_hit_has_no_value_field(self):
        _, hits = D.detect("NHS " + VG.nhs_digits())
        self.assertEqual(set(hits[0].__dataclass_fields__), {"code", "severity", "start", "end"})


if __name__ == "__main__":
    unittest.main()
