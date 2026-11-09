"""Regression set from the session 11 leak read (ADR 0012 to 0014) and the A2 limitation kinds. Invented values only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator", "validator/tests"):
    sys.path.insert(0, str(ROOT / sub))

import detectors as D  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import pipeline as PL  # noqa: E402
import quarantine as Q  # noqa: E402
import transform as T  # noqa: E402

CODE = {"credential": "CREDENTIAL", "order_number": "PATIENT_NUMBER", "date_malformed": "DATE"}


class RegressionSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.msgs = C.regression()

    def test_deterministic_and_sized(self):
        self.assertEqual(C.regression(), self.msgs)
        self.assertEqual(len(self.msgs), 20 * len(C.REGRESSION_KINDS + C.LIMITATION_KINDS))
        self.assertEqual(len({m["id"] for m in self.msgs}), len(self.msgs))

    def test_every_kind_planted_with_valid_spans(self):
        kinds = Counter()
        for m in self.msgs:
            spans = []
            for l in m["labels"]:
                self.assertTrue(0 <= l["start"] < l["end"] <= len(m["fields"][l["field"]]))
                spans.append((l["start"], l["end"]))
                if l["adversarial"]:
                    kinds[l["kind"]] += 1
                    self.assertIn(l["category"], C.CATEGORIES_REGRESSION + ["external_contact_name", "patient_name"])
            spans.sort()
            for a, b in zip(spans, spans[1:]):
                self.assertLessEqual(a[1], b[0])
        self.assertEqual(set(kinds), set(C.REGRESSION_KINDS + C.LIMITATION_KINDS))

    def test_limitation_kinds_come_last(self):
        # Adding the A2 kinds must not change the fixed-kind messages that precede them.
        n = 20 * len(C.REGRESSION_KINDS)
        self.assertTrue(all(l["kind"] not in C.LIMITATION_KINDS for m in self.msgs[:n] for l in m["labels"]))
        self.assertTrue(all(any(l["kind"] in C.LIMITATION_KINDS for l in m["labels"]) for m in self.msgs[n:]))

    def test_initials_metric_sees_initials_only_as_whole_tokens(self):
        msg = {"fields": {"body": "x"}, "labels": [{"field": "body", "category": "patient_name", "start": 0, "end": 4,
                                                     "kind": "initials_uncued"}]}
        msg["fields"]["body"] = "Q.Z. is booked."
        self.assertEqual(PL.initials_surviving(msg, {"body": "Q.Z. is booked."}), Counter({"initials_uncued": 1}))
        self.assertEqual(PL.initials_surviving(msg, {"body": "[NAME-1] is booked."}), Counter())
        msg2 = {"fields": {"body": "QZ again"}, "labels": [{"field": "body", "category": "patient_name", "start": 0,
                                                           "end": 2, "kind": "initials_uncued"}]}
        self.assertEqual(PL.initials_surviving(msg2, {"body": "AQZB again"}), Counter())

    def test_main_corpus_has_no_regression_categories(self):
        cats = {l["category"] for m in C.generate(200, 7) for l in m["labels"]}
        self.assertEqual(cats & set(C.CATEGORIES_REGRESSION), set())

    def test_directory_externals_are_not_staff(self):
        base = G.parse_directory(C.directory_markdown())
        reg = G.parse_directory(C.regression_directory_markdown())
        self.assertEqual(reg.full, base.full)
        self.assertEqual(reg.tokens, base.tokens)
        self.assertIn("Synthetic Supplier Ltd", C.regression_directory_markdown())

    def test_detectors_cover_every_planted_value(self):
        for m in self.msgs:
            text, hits = D.detect(m["fields"]["body"])
            for l in m["labels"]:
                if l["category"] in CODE:
                    over = [h for h in hits if h.start <= l["start"] and l["end"] <= h.end]
                    self.assertTrue(over and over[0].code == CODE[l["category"]], (l["kind"], [h.code for h in hits]))

    def test_pipeline_releases_no_planted_value_or_name(self):
        with tempfile.TemporaryDirectory() as td:
            st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
            q = Q.Quarantine(Path(td) / "ks", K.TestOnlyProtector())
            tr = T.Transformer(st, G.parse_directory(C.regression_directory_markdown()), year=2026)
            fixed = [m for m in self.msgs if any(l["kind"] in C.REGRESSION_KINDS for l in m["labels"])]
            s = PL.run_corpus(fixed, PL.Pipeline(tr, q, vocab=PL.corpus_vocab()), T.LEAK_CATS + C.CATEGORIES_REGRESSION)
        self.assertEqual(s["ok_leaks"], Counter())
        self.assertEqual(s["ok_name_msgs"], 0)
        self.assertEqual(s["ok_initials_msgs"], 0)
        self.assertGreater(s["ok"], 0)


if __name__ == "__main__":
    unittest.main()
