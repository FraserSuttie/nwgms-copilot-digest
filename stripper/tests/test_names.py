"""Trust display-name tests (ADR 0009). Invented names only; NER spans are supplied by hand (no spaCy needed).

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

import keystore as K  # noqa: E402
import mailfront as M  # noqa: E402
import namegate as G  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402
from test_mailfront import raw  # noqa: E402

ORG = "Manchester University NHS FT"
SHAPES = {
    "surname-forename-org": f"Zorbleton Quimby (R0A) {ORG}",
    "surname-forename-mft": "Zorbleton Quimby MFT",
    "surname-forename-digit": f"Zorbleton Quimby 2 (R0A) {ORG}",
    "comma-org": f"Zorbleton, Quimby (R0A) {ORG}",
    "forename-surname": "Quimby Zorbleton",
}
ADDR = "quimby.zorbleton@mft.nhs.uk"


def gate(text, names, header):
    people = G.People().with_header(header)
    spans = [(text.index(n), text.index(n) + len(n)) for n in names]
    dec, _ = G.gate(text, spans, people)
    return {text[d.start:d.end]: (d.action, d.reason) for d in dec}


class DisplayName(unittest.TestCase):
    def test_trust_shapes_normalise(self):
        self.assertEqual(M.display_name(SHAPES["surname-forename-org"]), "Zorbleton Quimby")
        self.assertEqual(M.display_name(SHAPES["surname-forename-mft"]), "Zorbleton Quimby")
        self.assertEqual(M.display_name(SHAPES["surname-forename-digit"]), "Zorbleton Quimby")
        self.assertEqual(M.display_name(SHAPES["comma-org"]), "Quimby Zorbleton")
        self.assertEqual(M.display_name("Quimby Zorbleton NHS Foundation Trust"), "Quimby Zorbleton")
        self.assertEqual(M.display_name("Zorbleton Quimby NHS FT 12"), "Zorbleton Quimby")

    def test_unchanged_and_fail_closed(self):
        self.assertEqual(M.display_name("Ada Fairweather [NWGMS]"), "Ada Fairweather")
        self.assertEqual(M.display_name("Quimby Zorbleton"), "Quimby Zorbleton")
        self.assertEqual(M.display_name("Zorbleton Quimby Somewhere Else Trustees"), "Zorbleton Quimby Somewhere Else Trustees")
        self.assertEqual(M.display_name("MFT"), "MFT")          # never reduced to nothing
        self.assertEqual(M.display_name("Zorbleton 7"), "Zorbleton")


class TrustParticipants(unittest.TestCase):
    def test_every_shape_passes_in_both_orders_and_positions(self):
        for label, display in SHAPES.items():
            hdr = [(M.display_name(display), ADDR)]
            with self.subTest(label):
                self.assertEqual(gate("Please ask Quimby Zorbleton to check", ["Quimby Zorbleton"], hdr)["Quimby Zorbleton"],
                                 ("pass", "FULL_HEADER"))
                self.assertEqual(gate("Zorbleton Quimby wrote", ["Zorbleton Quimby"], hdr)["Zorbleton Quimby"],
                                 ("pass", "FULL_HEADER"))
                self.assertEqual(gate("Hi Quimby,\nok", ["Quimby"], hdr)["Quimby"], ("pass", "BARE_GREETING"))
                self.assertEqual(gate("Thanks\nQuimby", ["Quimby"], hdr)["Quimby"], ("pass", "BARE_SIGNOFF"))

    def test_surname_is_not_ambiguous_after_two_orders(self):
        p = G.People().with_header([("Zorbleton Quimby", ADDR)])
        self.assertEqual(p.tokens["zorbleton"], {"zorbleton quimby"})
        self.assertEqual(p.tokens["quimby"], {"zorbleton quimby"})

    def test_bare_name_passes_with_full_name_in_either_order(self):
        hdr = [("Zorbleton Quimby", ADDR)]
        r = gate("Quimby Zorbleton agreed. Later Zorbleton said yes.", ["Quimby Zorbleton", "Zorbleton"], hdr)
        self.assertEqual(r["Zorbleton"], ("pass", "BARE_WITH_FULL"))

    def test_directory_person_in_header_keeps_one_canonical(self):
        base = G.People()
        base.add("Quimby Zorbleton", "DIR")
        p = G.People(dict(base.full), {k: set(v) for k, v in base.tokens.items()}).with_header([("Zorbleton Quimby", ADDR)])
        self.assertEqual(p.tokens["zorbleton"], {"quimby zorbleton"})
        self.assertEqual(p.full["quimby zorbleton"], "DIR")

    def test_forename_mid_sentence_still_stripped(self):          # ADR 0003 positions unchanged (option 4a)
        r = gate("I spoke to Quimby about it", ["Quimby"], [("Zorbleton Quimby", ADDR)])
        self.assertEqual(r["Quimby"], ("name", "BARE_MIDSENTENCE"))

    def test_external_sender_unchanged(self):
        hdr = [("Zorbleton Quimby", "quimby@example.org")]
        r = gate("Hi Quimby,\nPlease ask Quimby Zorbleton", ["Quimby", "Quimby Zorbleton"], hdr)
        self.assertEqual(r["Quimby"][0], "name")
        self.assertEqual(r["Quimby Zorbleton"][0], "name")

    def test_negative_patient_sharing_colleague_surname(self):
        hdr = [("Zorbleton Quimby", ADDR)]
        r = gate("Mrs Zorbleton was seen today", ["Zorbleton"], hdr)
        self.assertEqual(r["Zorbleton"], ("name", "BARE_MIDSENTENCE"))
        # accepted ADR 0003 residual: the colleague's full name passed elsewhere in the same message
        r = gate("Quimby Zorbleton replied. Mrs Zorbleton was seen today.", ["Quimby Zorbleton", "Zorbleton"], hdr)
        self.assertEqual(r["Zorbleton"], ("pass", "BARE_WITH_FULL"))
        # an unrelated patient forename + header colleague's surname is not a full-name match
        r = gate("Patient Rowan Zorbleton attended", ["Rowan Zorbleton"], hdr)
        self.assertEqual(r["Rowan Zorbleton"], ("name", "UNKNOWN_FULL"))

    def test_shared_mailbox_exact_only(self):
        hdr = [("NWGMS LIMS Support", "nwgms.lims@mft.nhs.uk")]
        p = G.People().with_header(hdr)
        self.assertIn("nwgms lims support", p.full)
        self.assertEqual(p.tokens, {})
        r = gate("Support will reply", ["Support"], hdr)
        self.assertEqual(r["Support"][0], "name")


class Density(unittest.TestCase):
    def test_org_words_allowed_without_header(self):
        a = R.allowed_words(G.People())
        for w in ("manchester", "university", "nhs", "foundation", "trust", "ft", "mft", "nwgms"):
            self.assertIn(w, a)

    def test_header_forename_surname_allowed(self):
        a = R.allowed_words(G.People(), header_people=[("Zorbleton Quimby", ADDR)])
        self.assertTrue({"zorbleton", "quimby"} <= a)
        self.assertEqual(R.unknown_cap_words("Zorbleton Quimby agreed.", a), [])

    def test_cue_check_unchanged_for_colleague_surname(self):    # gate, not the cue check, handles "Mrs <colleague surname>"
        a = R.allowed_words(G.People(), header_people=[("Zorbleton Quimby", ADDR)])
        self.assertEqual(R.cue_check("Mrs Zorbleton was seen", a)["CUE_NAME"], 0)
        self.assertEqual(R.cue_check("Mrs Wibblesworth was seen", a)["CUE_NAME"], 1)


class EndToEnd(unittest.TestCase):
    def test_build_and_transform(self):
        r = M.build(raw(**{"from": {"name": SHAPES["surname-forename-digit"], "address": ADDR},
                           "to": [{"name": f"Fairweather Ada (R0A) {ORG}", "address": "ada.fairweather@nhs.net"}],
                           "body": {"content_type": "text",
                                    "content": "Hi Ada,\n\nQuimby Zorbleton has the panel list.\n\nThanks,\nQuimby"}}))
        self.assertEqual(r.fields["header_from"], "Zorbleton Quimby")
        self.assertEqual(r.fields["header_to"], "Fairweather Ada")
        with tempfile.TemporaryDirectory() as td:
            tr = T.Transformer(K.KeyStore(Path(td) / "ks", K.TestOnlyProtector()), G.People(), use_ner=False, year=2026)
            out = tr.message(r.fields, r.header_people, commit=False, audit=False).fields
        self.assertEqual(out["header_from"], "Zorbleton Quimby")
        self.assertNotIn("[NAME-", out["header_to"] + out["header_from"])
        self.assertIn("Hi Ada,", out["message"])
        self.assertIn("Thanks,\nQuimby", out["message"])


if __name__ == "__main__":
    unittest.main()
