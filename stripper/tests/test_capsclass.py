"""Caps classifier measure (draft ADR 0017): position, shape and nearby-identifier checks, counts-only CLI, read-only.
Invented words only, temp directories only; NER spans supplied by hand.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import io
import re
import shutil
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator", "stripper/tests"):
    sys.path.insert(0, str(ROOT / sub))

import capsclass as CC  # noqa: E402
import residual as R  # noqa: E402
from test_caps import TP, WORDS, Base, write_guard  # noqa: E402

A = frozenset({"weekly", "status"})


def tiers(text, field="message", ner=None):
    return [(t, sorted(s)) for t, s in R.classify_caps(text, A, field, ner)]


class Signals(unittest.TestCase):
    def test_subject_word_is_flag(self):
        self.assertEqual(tiers("Zorblax Rollout\n\nAll fine today."), [("FLAG", ["SUBJECT"]), ("FLAG", ["SUBJECT"])])

    def test_two_unknown_words_in_lower_case_subject_hold(self):
        self.assertEqual(tiers("Re: Rowan Pike referral\n\nAll fine.")[0], ("HOLD", ["PAIR", "SUBJECT"]))

    def test_prose_word_is_prose(self):
        self.assertEqual(tiers("x\n\nThe Quendle report is late."), [("PROSE", ["PROSE"])])

    def test_name_shapes_hold(self):
        self.assertIn("TITLE", tiers("x\n\nPlease call Dr Quendle today.")[-1][1])
        self.assertIn("POSSESSIVE", tiers("x\n\nAll fine with Zorblax's feed.")[0][1])
        self.assertIn("ALONE", tiers("x\n\nThanks,\nQuendle")[0][1])
        self.assertIn("GREETING", tiers("x\n\nHi Quendle, all fine.")[0][1])
        self.assertIn("PAIR", tiers("x\n\nI saw [NAME-1] Quendle today.")[0][1])
        self.assertIn("HEADER", tiers("Quendle Vrimble", "header_to")[0][1])
        text = "x\n\nThe Quendle report is late."
        s = text.index("Quendle")
        self.assertEqual(tiers(text, ner=[(s, s + 7)])[0], ("HOLD", ["NER", "PROSE"]))

    def test_identifier_in_the_same_sentence_holds(self):
        self.assertEqual(tiers("x\n\nREF-BCDFGHJK seen; the Quendle feed was fine.")[0], ("HOLD", ["IDENT", "PROSE"]))
        self.assertEqual(tiers("x\n\nREF-BCDFGHJK seen. The Quendle report is late.")[0], ("PROSE", ["PROSE"]))

    def test_title_case_line_is_neutral(self):
        self.assertEqual(tiers("x\n\nQuendle Rollout Plan\nAll fine.")[0], ("FLAG", ["TITLE_LINE"]))

    def test_policy_counts_and_no_words(self):
        pol = R.caps_policy({"message": "Zorblax Rollout\n\nThe Quendle feed is late."}, A)
        self.assertEqual((pol["words"], pol["release_a"], pol["release_b"]), (3, False, True))
        pol = R.caps_policy({"message": "Zorblax Rollout\n\nAll fine."}, A)
        self.assertEqual((pol["release_a"], pol["release_b"]), (True, True))
        pol = R.caps_policy({"message": "x\n\nThanks,\nQuendle"}, A)
        self.assertEqual((pol["release_a"], pol["release_b"]), (False, False))
        for w in WORDS:
            self.assertNotIn(w.lower(), repr(pol).lower())


class Measure(Base):
    def run_main(self, out=None):
        out = out or io.StringIO()
        with redirect_stderr(io.StringIO()):
            rc = CC.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory)],
                         protector=TP, use_ner=False, out=out)
        return rc, out.getvalue()

    def test_hand_counted(self):
        self.fixture()        # caps-only: c1 x2 (alone lines; possessive) hold, c2 and c3 prose words
        self.put("c6", "Hi Callum,\n\nAll fine, nothing to report.\n\nThanks,\nAda", "Quendle Rollout")
        rc, rep = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("held for DENS_CAPS only: 5", rep)
        self.assertRegex(rep, r"policy A \(every word FLAG\): would release 1 more")
        self.assertRegex(rep, r"policy B \(no word HOLD\):\s+would release 3 more")

    def test_output_has_no_words_and_is_read_only(self):
        self.fixture()
        before = self.tree()
        with write_guard():
            rc, rep = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.tree(), before)
        self.assert_no_words(rep)
        body = rep.split("\n", 1)[1]
        self.assertIsNone(re.search(r"[^A-Za-z0-9_ ,.:;()=+/%\->\n]", body))

    def test_refuses_without_a_key_store(self):
        self.fixture()
        shutil.rmtree(self.state)
        rc, _ = self.run_main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
