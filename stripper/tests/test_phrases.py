"""Apostrophe surnames and the phrase allowlist (ADR 0011). Invented names and phrases only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import capsdiag as D  # noqa: E402
import capsscan as S  # noqa: E402
import capsvocab as V  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import runner as RN  # noqa: E402
import transform as T  # noqa: E402
from test_caps import TTY, body  # noqa: E402
from test_mailfront import raw  # noqa: E402

TP = K.TestOnlyProtector()
NONE = frozenset()
DIRECTORY = ("| Surname | Full name | Role | Team | Notes |\n|---|---|---|---|---|\n"
             "| O'Zorblax | Sean O'Zorblax | TBC | NWGMS | |\n| Quendle | Ada Quendle | TBC | NWGMS | |\n")


class Apostrophe(unittest.TestCase):
    def allowed(self, **kw):
        return R.allowed_words(G.parse_directory(DIRECTORY), **kw)

    def test_directory_surname_with_apostrophe_is_allowed_as_a_whole(self):
        a = self.allowed()
        for t in ("Thanks, Sean O'Zorblax was there.", "Thanks, Sean O’Zorblax was there.", "See o'zorblax"):
            self.assertEqual(R.unknown_cap_words(t, a), [], t)

    def test_unknown_apostrophe_words_are_still_counted(self):
        a = self.allowed()
        self.assertEqual(R.unknown_cap_words("Thanks, Sean O'Vrimble was there.", a), ["Vrimble"])
        self.assertEqual(R.unknown_cap_words("Thanks, Sean O'Vrimble was there.", NONE), ["Sean", "Vrimble"])
        self.assertEqual(R.unknown_cap_words("the Zorblax feed", a), ["Zorblax"])    # tail alone is not allowed

    def test_pipeline_releases_a_directory_apostrophe_surname(self):
        with tempfile.TemporaryDirectory() as td:
            st = K.KeyStore(Path(td) / "ks", TP)
            tr = T.Transformer(st, G.parse_directory(DIRECTORY), year=2026)
            ev = P.Pipeline(tr, None).evaluate({"message": "Hi Ada,\n\nSean O'Zorblax will check the feed.\n\nThanks,\nAda"})
            self.assertNotIn("DENS_CAPS", ev.reasons)


class Phrases(unittest.TestCase):
    A = frozenset({"vrimble court"})

    def words(self, t, allowed=None):
        return R.unknown_cap_words(t, self.A if allowed is None else allowed)

    def test_adjacent_phrase_passes(self):
        self.assertEqual(self.words("We saw the Vrimble Court team."), [])
        self.assertEqual(self.words("Vrimble Court\nand more"), [])

    def test_either_word_alone_is_counted(self):
        self.assertEqual(self.words("We saw the Vrimble team."), ["Vrimble"])
        self.assertEqual(self.words("Then ask Court about it, and Vrimble too."), ["Court", "Vrimble"])

    def test_order_matters_and_other_neighbours_do_not_match(self):
        self.assertEqual(self.words("We saw the Court Vrimble team."), ["Court", "Vrimble"])
        self.assertEqual(self.words("We saw the Vrimble Gardens team."), ["Vrimble", "Gardens"])
        self.assertEqual(self.words("We saw the Vrimble  Court team."), ["Vrimble", "Court"])   # not single-spaced
        self.assertEqual(self.words("We saw the Vrimble\nCourt team."), ["Vrimble"])    # not on one line (Court is a line opener)

    def test_phrase_in_a_longer_run_only_exempts_its_own_pair(self):
        self.assertEqual(self.words("Dr Quendle at Vrimble Court"), ["Quendle"])

    def test_case_insensitive_and_no_effect_without_a_phrase(self):
        self.assertEqual(self.words("the VRIMBLE COURT team"), [])        # all caps never counted anyway
        self.assertEqual(self.words("the vrimble Court team", frozenset({"vrimble court"})), [])
        self.assertEqual(R.unknown_cap_words("the Vrimble Court team", NONE), ["Vrimble", "Court"])

    def test_load_phrases(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "p.txt"
            f.write_text("Vrimble  Court\n\nSnorfel O’Hall Wing\n", encoding="utf-8")
            self.assertEqual(R.load_phrases(f), frozenset({"vrimble court", "snorfel o'hall wing"}))
            f.write_text("Vrimble\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                R.load_phrases(f)
            self.assertNotIn("Vrimble", str(cm.exception))

    def test_phrases_do_not_leak_into_single_word_vocab(self):
        a = R.allowed_words(G.parse_directory(DIRECTORY), vocab=frozenset({"vrimble court"}))
        self.assertNotIn("vrimble", a)
        self.assertNotIn("court", a)


class Tools(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.drop, self.state = self.td / "drop", self.td / "state"
        self.drop.mkdir()
        K.KeyStore(self.state, TP)
        self.directory = self.td / "directory.md"
        self.directory.write_text(C.directory_markdown(), encoding="utf-8")
        for i, t in enumerate(["We met at Vrimble Court today.", "Then Vrimble Court again.", "Only Vrimble here."], 1):
            m = raw(internet_message_id=f"<p{i}@x.invalid>", conversation_id=f"c{i}", sent=f"2026-10-30T09:{i:02d}:00Z",
                    body=body(f"Hi Callum,\n\n{t}\n\nThanks,\nAda"))
            (self.drop / f"p{i}.json").write_text(json.dumps(m), encoding="utf-8")

    def scan(self, vocab):
        return S.scan_drop(self.drop, self.state, self.directory.read_text(encoding="utf-8"), vocab, use_ner=False,
                           protector=TP, year=2026)

    def test_phrase_clears_only_messages_where_the_pair_is_adjacent(self):
        before = sum(1 for m in self.scan(NONE).msgs if not m.reasons)
        after = sum(1 for m in self.scan(frozenset({"vrimble court"})).msgs if not m.reasons)
        self.assertEqual((before, after), (0, 2))

    def test_runner_flag_refuses_non_local_or_one_word_phrase_files(self):
        argv = ["--drop", str(self.drop), "--state", str(self.td / "s"), "--out", str(self.td / "o"),
                "--directory", str(self.directory)]
        for path in (G.REPO_ROOT / "p.txt", self.td / "OneDrive - T" / "p.txt"):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(RN.main(argv + ["--phrases", str(path)]), 2)
        one = self.td / "one.txt"
        one.write_text("Vrimble\n", encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(RN.main(argv + ["--phrases", str(one)]), 2)
        self.assertNotIn("Vrimble", err.getvalue())

    def test_diag_and_vocab_accept_phrases(self):
        ph = self.td / "phrases.txt"
        ph.write_text("Vrimble Court\n", encoding="utf-8")
        base = ["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory), "--phrases", str(ph)]
        out = io.StringIO()
        self.assertEqual(D.main(base, protector=TP, use_ner=False, out=out), 0)
        self.assertIn("released today: 2", out.getvalue())
        self.assertNotIn("vrimble", out.getvalue().lower())
        out = TTY()
        self.assertEqual(V.main(base + ["--list", "--all"], protector=TP, use_ner=False, out=out, inp=io.StringIO()), 0)
        self.assertIn("Vrimble", out.getvalue())                  # the one lone use is still listed
        with redirect_stderr(io.StringIO()):
            bad = ["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory),
                   "--phrases", str(G.REPO_ROOT / "p.txt")]
            self.assertEqual(D.main(bad, protector=TP, use_ner=False, out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
