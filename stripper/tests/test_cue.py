"""Cue check tests (ADR 0008). Synthetic data only; every name here is invented.

The corpus generator plants lower-case patient names with a single template ("seen pt <name>"), so these cases use
other cues, positions and punctuation that the generator does not produce, plus clean prose and a documented miss.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
import residual as R  # noqa: E402

ALLOWED = frozenset({"ridgeway", "callum", "epic", "igene"})


def hit(text: str) -> bool:
    return bool(R.cue_check(text, ALLOWED))


class CueCatches(unittest.TestCase):
    def test_lower_case_names_after_each_cue(self):
        for t in ("seen pt jane doe today", "for patient amy lark", "baby ellis vane is due", "proband tom harte",
                  "mrs okonedo rang", "mr fenby called", "ms quill asked", "miss tarrow phoned", "mx rook rang",
                  "Patient: noor bello", "pt. amy lark", "re pt  jo kent", "PATIENT ivo marsh"):
            with self.subTest(t=t):
                self.assertTrue(hit(t))

    def test_name_one_word_further_on(self):
        self.assertTrue(hit("the patient is ivo marsh"))
        self.assertTrue(hit("patient called noor bello"))

    def test_possessive_and_hyphenated_names(self):
        self.assertTrue(hit("proband tom's sample"))
        self.assertTrue(hit("patient ann-marie lowe"))

    def test_capitalised_names_too(self):
        self.assertTrue(hit("patient is Osric Fenwhistle"))

    def test_initials(self):
        self.assertTrue(hit("patient J.S. again"))

    def test_counts_only(self):
        r = R.cue_check("seen pt jane doe", ALLOWED)
        self.assertEqual(dict(r), {"CUE_NAME": 1})


class CueLetsThrough(unittest.TestCase):
    def test_ordinary_prose(self):
        for t in ("the patient is waiting for results", "patient feedback survey is due", "pt referral received",
                  "the patient's sample arrived", "patient sample for the cohort", "Patient details are in iGene",
                  "a baby was referred", "patients are booked"):
            with self.subTest(t=t):
                self.assertFalse(hit(t))

    def test_staff_and_allowed_words(self):
        self.assertFalse(hit("Mr Ridgeway will chase it"))
        self.assertFalse(hit("Mr. Callum Ridgeway will chase it"))
        self.assertFalse(hit("patient record in Epic"))

    def test_placeholders_end_the_window(self):
        self.assertFalse(hit("baby [NAME-1] is due"))
        self.assertFalse(hit("Mr. [NAME-2] called"))
        self.assertFalse(hit("patient REF-BCDFGHJK is ready"))

    def test_window_stops_at_punctuation(self):
        self.assertFalse(hit("Thanks, patient. Fenby can wait"))
        self.assertFalse(hit("the patient\nzorvel"))

    def test_window_is_two_words(self):
        self.assertFalse(hit("patient is now booked zorvel"))


class CueKnownMisses(unittest.TestCase):
    """Documented limits: no cue next to the name, or a cue outside the fixed list. These must stay visible."""

    def test_no_cue_next_to_the_name(self):
        self.assertFalse(hit("sample for jo kent arrived"))

    def test_relative_cue_is_not_in_the_list(self):
        self.assertFalse(hit("mum says ivo is unwell"))


if __name__ == "__main__":
    unittest.main()
