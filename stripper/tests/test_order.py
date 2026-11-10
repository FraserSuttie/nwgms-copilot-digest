"""Hyphenated ignore words, 'Surname First' Directory names and the review list (ADR 0016). Invented names and words
only; NER spans are supplied by hand, so no spaCy is needed.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator"):
    sys.path.insert(0, str(ROOT / sub))

import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import residual as R  # noqa: E402
import review as RV  # noqa: E402

DIRECTORY = """| Surname | Full name | Role | Organisation | Notes |
|---|---|---|---|---|
| Vantrell | Marisolde (Mari) Vantrell | Manager | NWGMS | |
| Quellen | Orrin Quellen | Admin | MFT | |
| Brask | Tobin Brask | Supplier | Example Ltd | |
"""


def ignore_from(text):
    td = Path(tempfile.mkdtemp())
    try:
        (td / "ignore.txt").write_text(text, encoding="utf-8")
        return frozenset(G.norm(w) for w in R.load_wordlist(td / "ignore.txt"))
    finally:
        shutil.rmtree(td, ignore_errors=True)


def gate(text, names, ignore=frozenset()):
    spans = [(text.index(n), text.index(n) + len(n)) for n in names]
    dec, _ = G.gate(text, spans, G.parse_directory(DIRECTORY), ignore=ignore)
    return {text[d.start:d.end]: (d.action, d.reason) for d in dec}


class HyphenatedIgnore(unittest.TestCase):
    IGN = ignore_from("Zen-Trix-01\nLab-36\n")

    def test_loader_splits_and_gate_rejoins(self):
        self.assertTrue({"zen", "trix", "01", "lab", "36"} <= self.IGN)
        self.assertTrue(G.ignored_word("Zen-Trix-01", self.IGN))
        self.assertTrue(G.ignored_word("LAB-36", self.IGN))
        self.assertFalse(G.ignored_word("Zen-Quill", self.IGN))

    def test_hyphenated_span_passes_and_neighbour_is_gated(self):
        self.assertEqual(gate("node Zen-Trix-01 alert", ["Zen-Trix-01"], self.IGN)["Zen-Trix-01"], ("pass", "IGNORED"))
        r = gate("seen Zen-Trix-01 Ombrella today", ["Zen-Trix-01 Ombrella"], self.IGN)
        self.assertEqual(r["Ombrella"][0], "name")

    def test_hyphenated_place_hit_is_gated_as_name(self):
        p = G.parse_directory(DIRECTORY)
        self.assertTrue(G.loc_as_name("Zen-Trix-01", 0, 11, p, self.IGN))
        self.assertFalse(G.loc_as_name("Zen-Quill", 0, 9, p, self.IGN))


class SurnameFirst(unittest.TestCase):
    def test_directory_name_in_either_order(self):
        self.assertEqual(gate("From: Vantrell Marisolde", ["Vantrell Marisolde"])["Vantrell Marisolde"],
                         ("pass", "FULL_DIR"))
        self.assertEqual(gate("Quellen Orrin agreed", ["Quellen Orrin"])["Quellen Orrin"], ("pass", "FULL_DIR"))

    def test_one_person_not_two(self):
        p = G.parse_directory(DIRECTORY)
        self.assertEqual(p.tokens["vantrell"], {"marisolde vantrell"})
        self.assertEqual(p.canon["vantrell marisolde"], "marisolde vantrell")

    def test_patients_and_externals_still_stripped(self):
        self.assertEqual(gate("Vantrell Rowan attended", ["Vantrell Rowan"])["Vantrell Rowan"], ("name", "UNKNOWN_FULL"))
        self.assertEqual(gate("Brask Tobin called", ["Brask Tobin"])["Brask Tobin"], ("name", "UNKNOWN_FULL"))
        self.assertEqual(gate("Mari Rowan attended", ["Mari Rowan"])["Mari Rowan"], ("name", "UNKNOWN_FULL"))


class ReviewList(unittest.TestCase):
    def test_vocab_only_names_are_shown_and_marked(self):
        td = Path(tempfile.mkdtemp())
        try:
            st = G.State(td / "s", K.TestOnlyProtector())
            st.merge_counts({"zentrix": 5, "quorble": 4, "zen-trix-01": 2, "orbleck": 1})
            (td / "ignore.txt").write_text("Zentrix\nZen-Trix-01\n", encoding="utf-8")
            (td / "vocab.txt").write_text("Quorble\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                RV.cmd_list(st, 1, RV.covered_words(ignore=td / "ignore.txt"), RV.covered_words(vocab=td / "vocab.txt"))
            rows = buf.getvalue().split("--")[0]
            self.assertNotIn("zentrix", rows)           # ignore: hidden, including the hyphenated entry
            self.assertNotIn("zen-trix", rows)
            self.assertIn("quorble  (vocab only", rows)  # vocab: shown and marked
            self.assertIn("orbleck", rows)
            self.assertNotIn("orbleck  (vocab", rows)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
