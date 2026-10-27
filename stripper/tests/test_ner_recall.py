"""Tests for the optional NER layer and the recall report. Run from the repo root:

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator", "validator/tests"):
    sys.path.insert(0, str(ROOT / sub))

import generate as C  # noqa: E402
import ner as N  # noqa: E402
import recall as R  # noqa: E402


class NerStubTests(unittest.TestCase):
    def test_stub_returns_nothing_and_says_so(self):
        saved = N._engine
        N._engine = None
        try:
            self.assertEqual(N.detect_ner("Someone Else lives here"), [])
        finally:
            N._engine = saved

    def test_real_layer_finds_a_person_when_installed(self):
        if not N.load():
            self.skipTest(N.STATUS)
        hits = N.detect_ner("Please ask Dominic Aspinwall to look at it.")
        self.assertIn("NER_PERSON", [h.code for h in hits])


class RecallReportTests(unittest.TestCase):
    msgs = C.generate(300, seed=11)

    def test_report_contains_counts_only(self):
        rep = R.report(self.msgs, use_ner=False)
        for m in self.msgs[:60]:
            for l in m["labels"]:
                v = m["fields"][l["field"]][l["start"]:l["end"]]
                if len(v) >= 6:
                    self.assertNotIn(v, rep)

    def test_deterministic_categories_have_full_recall(self):
        per, _, _, _, _ = R.evaluate(self.msgs, use_ner=False)
        deterministic = ["dob", "nhs_number", "phone", "address", "postcode", "patient_email", "mrn", "referral_no",
                         "sample_no", "worksheet_no", "qc_no", "case_no", "gmc_id", "external_contact_email",
                         "clinical_patient_level", "url_personal"]
        for cat in deterministic:
            self.assertGreater(per[cat]["planted"], 0, cat)
            self.assertEqual(per[cat]["full"], per[cat]["planted"], cat)

    def test_names_are_not_claimed_without_ner(self):
        per, _, _, _, _ = R.evaluate(self.msgs, use_ner=False)
        self.assertEqual(per["patient_name"]["full"], 0)


if __name__ == "__main__":
    unittest.main()
