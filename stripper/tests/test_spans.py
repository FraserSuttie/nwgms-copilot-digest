"""Span rules from the session 13 real-mail over-redaction read (ADR 0015). Invented names and words only; NER spans are
supplied by hand or by a stub, so no spaCy is needed.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator"):
    sys.path.insert(0, str(ROOT / sub))

import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import patterns as P  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402

DIRECTORY = """| Surname | Full name | Role | Organisation | Notes |
|---|---|---|---|---|
| Zorbleton | Quimby Zorbleton | Analyst | NWGMS | |
| Farrowdale | Quimby Farrowdale | Scientist | NWGMS | |
| Ridgewell | Ada Ridgewell | Scientist | NWGMS | |
| Ridgewell | Bram Ridgewell | Technologist | NWGMS | |
| Vantrell | Marisolde (Mari) Vantrell | Manager | NWGMS | |
| Quellen | Mari Quellen | Admin | NWGMS | |
"""
TRUST = "@mft.nhs.uk"
IGNORE = frozenset({"zentrix", "qorvex", "desk"})


def people(header=()):
    return G.parse_directory(DIRECTORY).with_header(header)


def gate(text, names, header=(), ignore=IGNORE):
    spans = [(text.index(n), text.index(n) + len(n)) for n in names]
    dec, _ = G.gate(text, spans, people(header), ignore=ignore)
    return {text[d.start:d.end]: (d.action, d.reason) for d in dec}


class IgnoreSpans(unittest.TestCase):
    def test_ignore_word_trimmed_from_span_edge(self):
        r = gate("Zentrix Down again", ["Zentrix Down"])
        self.assertNotIn("Zentrix Down", r)
        self.assertEqual(r["Down"][0], "name")

    def test_span_of_only_ignore_words_passes(self):
        r = gate("please cc Qorvex Desk in future", ["Qorvex Desk"])
        self.assertEqual(r["Qorvex Desk"], ("pass", "IGNORED"))

    def test_unknown_name_beside_ignore_word_still_stripped(self):
        r = gate("seen on Zentrix Ombrella Pike today", ["Zentrix Ombrella Pike"])
        self.assertEqual(r["Ombrella Pike"], ("name", "UNKNOWN_FULL"))
        r = gate("Ombrella Qorvex said", ["Ombrella Qorvex"])
        self.assertEqual(r["Ombrella"][0], "name")

    def test_whole_entry_match_unchanged(self):
        r = gate("Zentrix is back", ["Zentrix"])
        self.assertEqual(r["Zentrix"], ("pass", "IGNORED"))


class HeaderFirstNames(unittest.TestCase):
    HDR = [("Zorbleton Quimby", "quimby.zorbleton" + TRUST)]

    def test_shared_first_name_passes_when_one_is_on_the_header(self):
        self.assertEqual(gate("Hi Quimby,", ["Quimby"])["Quimby"], ("name", "AMBIGUOUS"))
        self.assertEqual(gate("Hi Quimby,", ["Quimby"], self.HDR)["Quimby"], ("pass", "BARE_GREETING"))
        self.assertEqual(gate("Thanks,\nQuimby", ["Quimby"], self.HDR)["Quimby"], ("pass", "BARE_SIGNOFF"))

    def test_mid_sentence_still_stripped(self):
        self.assertEqual(gate("I spoke to Quimby about it", ["Quimby"], self.HDR)["Quimby"], ("name", "BARE_MIDSENTENCE"))

    def test_both_candidates_on_header_stays_ambiguous(self):
        hdr = self.HDR + [("Farrowdale Quimby", "quimby.farrowdale" + TRUST)]
        self.assertEqual(gate("Hi Quimby,", ["Quimby"], hdr)["Quimby"], ("name", "AMBIGUOUS"))

    def test_surname_stays_ambiguous(self):
        hdr = [("Ridgewell Ada", "ada.ridgewell" + TRUST)]
        self.assertEqual(gate("Hi Ridgewell,", ["Ridgewell"], hdr)["Ridgewell"], ("name", "AMBIGUOUS"))
        self.assertEqual(gate("Hi Ada,", ["Ada"], hdr)["Ada"], ("pass", "BARE_GREETING"))

    def test_nickname_of_header_participant(self):
        hdr = [("Vantrell Marisolde", "marisolde.vantrell" + TRUST)]
        self.assertEqual(gate("Thanks Mari", ["Mari"])["Mari"], ("name", "AMBIGUOUS"))
        self.assertEqual(gate("Thanks Mari", ["Mari"], hdr)["Mari"], ("pass", "BARE_GREETING"))

    def test_external_header_does_not_disambiguate(self):
        hdr = [("Zorbleton Quimby", "quimby@example.org")]
        self.assertEqual(gate("Hi Quimby,", ["Quimby"], hdr)["Quimby"], ("name", "AMBIGUOUS"))


class PlaceSpans(unittest.TestCase):
    def test_loc_as_name(self):
        p = people()
        for text, want in (("Quimby", True), ("Zentrix", True), ("Ombrella", False), ("Quimby Lane", False)):
            self.assertEqual(G.loc_as_name(text, 0, len(text), p, IGNORE), want, text)

    def _stub(self, places):
        def detect(text):
            return [P.Hit("NER_LOCATION", "fail", text.index(w), text.index(w) + len(w)) for w in places if w in text]
        return detect

    def test_transform_keeps_staff_signoff_and_ignore_word_tagged_as_places(self):
        body = "Morning,\n\nZentrix is fine and Ombrella is not.\n\nBest wishes,\n\nQuimby"
        hdr = [("Zorbleton Quimby", "quimby.zorbleton" + TRUST)]
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(N, "detect_ner", self._stub(["Zentrix", "Ombrella", "Quimby"])):
            tr = T.Transformer(K.KeyStore(Path(td) / "ks", K.TestOnlyProtector()), G.parse_directory(DIRECTORY),
                               ignore=IGNORE, use_ner=True, year=2026)
            out = tr.text(body, hdr).text
        self.assertIn("Best wishes,\n\nQuimby", out)
        self.assertIn("Zentrix is fine", out)
        self.assertIn("[LOC] is not", out)

    def test_residual_treats_staff_place_hit_as_a_gated_name(self):
        text = "Best wishes,\n\nQuimby"
        hdr = [("Zorbleton Quimby", "quimby.zorbleton" + TRUST)]
        with mock.patch.object(N, "detect_ner", self._stub(["Quimby"])):
            r = R.scan(text, set(), people(hdr), IGNORE, use_ner=True)
            self.assertEqual((r["HIT_NER_LOCATION"], r["NAME_SURVIVES"]), (0, 0))
            r = R.scan("I spoke to Quimby about it", set(), people(), IGNORE, use_ner=True)
            self.assertEqual(r["NAME_SURVIVES"], 1)    # mid-sentence and ambiguous: still held


if __name__ == "__main__":
    unittest.main()
