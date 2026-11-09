"""Order-number and malformed-date tests (ADR 0013). Invented values only; no shape is copied from a real message.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator"):
    sys.path.insert(0, str(ROOT / sub))

import detectors as D  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import transform as T  # noqa: E402


def found(text: str, code: str):
    t, hits = D.detect(text)
    return [t[h.start:h.end] for h in hits if h.code == code]


class OrderNumberTests(unittest.TestCase):
    def test_each_cue(self):
        for s, v in (("I put through order number 4817 for testing", "4817"), ("order no. 30291 is stuck", "30291"),
                     ("orderID 774102", "774102"), ("Order #5520 rejected", "5520"), ("request id: 88213", "88213"),
                     ("accession no 6019274", "6019274"), ("episode number 3310", "3310"),
                     ("encounter id 90021", "90021"), ("lab no. 4471", "4471"), ("order 2244 failed", "2244")):
            self.assertEqual(found(s, "PATIENT_NUMBER"), [v], s)

    def test_letter_prefix_and_trailing_punctuation(self):
        self.assertEqual(found("order number QX40912.", "PATIENT_NUMBER"), ["QX40912"])
        self.assertEqual(found("(order 6631)", "PATIENT_NUMBER"), ["6631"])

    def test_no_cue_or_too_short_no_hit(self):
        for s in ("in order to fix it", "order 2 of 5", "test code R123.4 was used", "Managed Alert ID: 351",
                  "queue count of 10", "orderly 4410", "border 5512"):
            self.assertEqual(found(s, "PATIENT_NUMBER"), [], s)

    def test_test_code_after_order_number_kept(self):
        self.assertEqual(found("order number 5120 (R123.4) for testing", "PATIENT_NUMBER"), ["5120"])


class LooseDateTests(unittest.TestCase):
    def test_doubled_separators(self):
        for s, v in (("created at 13.42 on 07//03/19 by", "07//03/19"), ("on 07/03//2019.", "07/03//2019"),
                     ("seen 07 /03/19 ok", "07 /03/19"), ("from 7-/3/21", "7-/3/21")):
            self.assertIn(v, found(s, "DATE"), s)

    def test_well_formed_dates_unchanged(self):
        self.assertEqual(found("on 07/03/2019 ok", "DATE"), ["07/03/2019"])

    def test_versions_and_spaced_numbers_no_hit(self):
        for s in ("upgraded to 4.07.0 today", "release 4.07.10 notes", "items 1  2  23 here", "v12..3"):
            self.assertEqual(found(s, "DATE"), [], s)


class TransformTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        store = K.KeyStore(Path(td.name) / "ks", K.TestOnlyProtector())
        self.tr = T.Transformer(store, G.parse_directory(C.directory_markdown()), year=2026)

    def t(self, s):
        return self.tr.text(s).text

    def test_order_number_becomes_pt_num(self):
        self.assertEqual(self.t("I put through order number 4817 for testing."), "I put through order number [PT-NUM] for testing.")

    def test_loose_date_becomes_date_or_dob(self):
        self.assertEqual(self.t("created on 07//03/19 here"), "created on [DATE] here")
        self.assertIn("[PT-DOB]", self.t("DOB 07//03/19"))


if __name__ == "__main__":
    unittest.main()
