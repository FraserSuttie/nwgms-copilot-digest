"""Tests for the synthetic corpus generator. Run from the repo root:

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
sys.path.insert(0, str(ROOT / "validator"))

import generate as C  # noqa: E402
import patterns as P  # noqa: E402


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.msgs = C.generate(400, seed=7)

    def test_deterministic(self):
        self.assertEqual(C.generate(30, 5), C.generate(30, 5))
        self.assertNotEqual(C.generate(30, 5), C.generate(30, 6))

    def test_label_spans_are_inside_text_and_do_not_overlap(self):
        for m in self.msgs:
            by_field = {}
            for l in m["labels"]:
                text = m["fields"][l["field"]]
                self.assertTrue(0 <= l["start"] < l["end"] <= len(text))
                by_field.setdefault(l["field"], []).append((l["start"], l["end"]))
            for spans in by_field.values():
                spans.sort()
                for a, b in zip(spans, spans[1:]):
                    self.assertLessEqual(a[1], b[0])

    def test_all_categories_present(self):
        seen = {l["category"] for m in self.msgs for l in m["labels"]}
        expected = set(C.CATEGORIES_STRIP) | set(C.CATEGORIES_KEEP) - {"trust_email"}
        self.assertEqual(expected - seen, set())

    def test_actions_match_category(self):
        for m in self.msgs:
            for l in m["labels"]:
                want = "keep" if l["category"] in C.CATEGORIES_KEEP else "strip"
                self.assertEqual(l["action"], want)

    def test_plain_nhs_numbers_have_valid_checksums(self):
        n = 0
        for m in self.msgs:
            for l in m["labels"]:
                if l["category"] == "nhs_number" and l["kind"] in ("plain",):
                    v = m["fields"][l["field"]][l["start"]:l["end"]]
                    digits = "".join(c for c in v if c.isdigit())
                    self.assertTrue(P.nhs_checksum_ok(digits))
                    n += 1
        self.assertGreater(n, 20)

    def test_near_miss_checksums_are_invalid(self):
        found = False
        for m in self.msgs:
            for l in m["labels"]:
                if l["kind"] == "near_miss":
                    v = m["fields"][l["field"]][l["start"]:l["end"]]
                    self.assertFalse(P.nhs_checksum_ok("".join(c for c in v if c.isdigit())))
                    found = True
        self.assertTrue(found)

    def test_nfkc_keeps_length_for_every_message(self):
        for m in self.msgs:
            for t in m["fields"].values():
                self.assertEqual(len(unicodedata.normalize("NFKC", t)), len(t))

    def test_mrn_shapes(self):
        import re
        shapes = set()
        for m in self.msgs:
            for l in m["labels"]:
                if l["category"] == "mrn" and l["kind"] == "plain":
                    v = m["fields"][l["field"]][l["start"]:l["end"]]
                    shapes.add("ods7" if re.fullmatch(r"[A-Z]{3}\d{7}", v) else "d8" if re.fullmatch(r"\d{8}", v) else "other")
        self.assertEqual(shapes, {"ods7", "d8"})

    def test_all_sources_covered(self):
        self.assertEqual({m["source"] for m in self.msgs}, {"email", "teams", "transcript"})


if __name__ == "__main__":
    unittest.main()
