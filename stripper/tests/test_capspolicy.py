"""Caps policy B (ADR 0017): release a message held only for unknown capitalised words when none shows a name signal.
Invented words only; the NER layer is a stub that reports itself active and finds nothing, which is the worst case for
names (every planted name the real NER would strip stays in the text for the classifier to judge).

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator", "stripper/tests"):
    sys.path.insert(0, str(ROOT / sub))

import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import mailfront as M  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import runner as RN  # noqa: E402
import transform as T  # noqa: E402
from test_mailfront import raw  # noqa: E402

TP = K.TestOnlyProtector()


def sig(text):
    return [sorted(s) for _, s in R.classify_caps(text, frozenset())]


class NewSignals(unittest.TestCase):
    def test_relative(self):
        self.assertIn("RELATIVE", sig("x\n\nmum says Quendle is fine.")[0])
        self.assertIn("RELATIVE", sig("x\n\nher daughter Quendle rang.")[0])
        self.assertNotIn("RELATIVE", sig("x\n\nThe Quendle feed is late.")[0])

    def test_actor(self):
        self.assertIn("ACTOR", sig("x\n\nWe think Quendle is unwell.")[0])
        self.assertIn("ACTOR", sig("x\n\nand Quendle rang twice.")[0])
        self.assertNotIn("ACTOR", sig("x\n\nand the Quendle is down.")[0])       # article: a thing, not a person
        self.assertNotIn("ACTOR", sig("x\n\nThe Quendle feed is late.")[0])


class Policy(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.patches = [mock.patch.object(N, "STATUS", "active (test stub)"), mock.patch.object(N, "detect_ner", lambda t: [])]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def pipe(self, policy="B", use_ner=True, directory=None):
        st = K.KeyStore(self.td / f"ks{policy}{use_ner}", TP)
        q = Q.Quarantine(self.td / f"ks{policy}{use_ner}", TP)
        tr = T.Transformer(st, G.parse_directory(directory or C.directory_markdown()), use_ner=use_ner, year=2026)
        return P.Pipeline(tr, q, limits=R.Limits(caps_policy=policy), vocab=P.corpus_vocab())

    def msg(self, body):
        return {"message": "Weekly note\n\n" + body}

    def test_prose_word_released_and_counted(self):
        o = self.pipe().process(self.msg("The Quendle feed is late again."))
        self.assertEqual(o.status, "ok")
        self.assertEqual(o.counts["CAPS_RELEASED"], 1)        # Quendle ("Weekly note" is an ordinary sentence start)

    def test_policy_off_and_ner_off_hold(self):
        self.assertEqual(self.pipe("off").process(self.msg("The Quendle feed is late again.")).reasons, Counter({"DENS_CAPS": 1}))
        self.assertEqual(self.pipe(use_ner=False).process(self.msg("The Quendle feed is late again.")).status, "quarantined")

    def test_name_signals_hold(self):
        p = self.pipe()
        for body in ("mum says Quendle is unwell.", "We think Quendle is unwell.", "All fine.\n\nThanks,\nQuendle",
                     "Please call Dr Quendle.", "I saw Rowan Quendle today.", "Hi Quendle, all fine."):
            self.assertEqual(p.process(self.msg(body)).status, "quarantined", body)

    def test_other_reasons_still_hold(self):
        o = self.pipe().process(self.msg("The Quendle feed is late. We saw patient Ombrella today."))
        self.assertEqual(o.status, "quarantined")
        self.assertIn("CUE_NAME", o.reasons)

    def test_corpus_worst_case_releases_no_planted_name(self):
        s = P.run_corpus(C.generate(1200, 20261027), self.pipe())
        self.assertEqual((s["ok_name_msgs"], s["ok_initials_msgs"], dict(s["ok_leaks"])), (0, 0, {}))
        reg = C.regression()
        fixed = [m for m in reg if any(l["kind"] in C.REGRESSION_KINDS for l in m["labels"])]
        rf = P.run_corpus(fixed, self.pipe(directory=C.regression_directory_markdown()),
                          T.LEAK_CATS + C.CATEGORIES_REGRESSION)
        self.assertEqual((rf["ok_name_msgs"], rf["ok_initials_msgs"], dict(rf["ok_leaks"])), (0, 0, {}))


class Block(unittest.TestCase):
    def test_unknown_word_count_is_a_flag_not_a_redaction(self):
        rec = M.build(raw(body={"content_type": "text", "content": "Hi,\n\nThe Quendle feed is late."}))
        out = RN.render_block("a" * 32, rec, {**rec.fields}, Counter({"NAME": 1, "CAPS_RELEASED": 2}))
        line = next(l for l in out.splitlines() if l.startswith("- redactions:"))
        self.assertIn("redactions: NAME 1 |", line)
        self.assertIn("unknown words 2", line)
        self.assertNotIn("CAPS", out)


if __name__ == "__main__":
    unittest.main()
