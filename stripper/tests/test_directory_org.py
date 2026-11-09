"""Directory organisation filter and bracketed nicknames (ADR 0014). Invented names only.

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
import namegate as G  # noqa: E402
import transform as T  # noqa: E402

DIR = """# People Directory

| Surname | Full name | Role | Organisation | Notes |
|---|---|---|---|---|
| Quimby | Zorbleton Quimby | Synthetic role | NWGMS | |
| Vantrell | Marisolde (Mari) Vantrell | Synthetic role | NWGMS (Liverpool site) | goes by Mari |
| Plaxworth | Odessa Plaxworth | Synthetic role | MFT | |
| Crennick | Thibault Crennick | Integration Engineer | Synthetic Software Ltd. | |
| Dorrance | Wilhelmina (Mina) Dorrance | IT Manager | Another Synthetic NHS Trust | |
| Halvorsk | Ingram Halvorsk | TBC | TBC | |
| [surname unknown] | Bertilde [surname unknown] | TBC | NWGMS | |
"""


class DirectoryTests(unittest.TestCase):
    ppl = G.parse_directory(DIR)

    def test_internal_rows_are_staff(self):
        for n in ("zorbleton quimby", "marisolde vantrell", "odessa plaxworth"):
            self.assertIn(n, self.ppl.full, n)

    def test_external_and_tbc_rows_are_not(self):
        for n in ("thibault crennick", "wilhelmina dorrance", "ingram halvorsk"):
            self.assertNotIn(n, self.ppl.full, n)
        for t in ("crennick", "thibault", "mina", "dorrance", "halvorsk"):
            self.assertNotIn(t, self.ppl.tokens, t)

    def test_nickname_is_a_token_of_its_row(self):
        self.assertEqual(self.ppl.tokens.get("mari"), {"marisolde vantrell"})
        self.assertEqual(self.ppl.canon.get("mari vantrell"), "marisolde vantrell")
        self.assertNotIn("marisolde mari vantrell", self.ppl.full)

    def test_square_bracket_note_is_not_a_name(self):
        self.assertNotIn("unknown", self.ppl.tokens)
        self.assertNotIn("surname", self.ppl.tokens)

    def test_no_organisation_column_reads_as_staff(self):
        old = "| Surname | Full name | Role |\n|---|---|---|\n| Crennick | Thibault Crennick | x |\n"
        self.assertIn("thibault crennick", G.parse_directory(old).full)


class GateTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        store = K.KeyStore(Path(td.name) / "ks", K.TestOnlyProtector())
        self.tr = T.Transformer(store, G.parse_directory(DIR), year=2026)

    def t(self, s):
        return self.tr.text(s).text

    def test_external_full_name_is_not_passed(self):
        # As NER would report it: before ADR 0014 the Directory passed it as FULL_DIR; now it is unknown.
        text = "If yes @Thibault Crennick will the fix be applied?"
        s = text.index("Thibault")
        decisions, _ = G.gate(text, [(s, s + len("Thibault Crennick"))], self.tr.people)
        self.assertEqual([(d.action, d.reason) for d in decisions], [("name", "UNKNOWN_FULL")])

    def test_internal_full_name_with_span_passes(self):
        text = "Ask Odessa Plaxworth about it."
        s = text.index("Odessa")
        decisions, _ = G.gate(text, [(s, s + len("Odessa Plaxworth"))], self.tr.people)
        self.assertEqual([d.action for d in decisions], ["pass"])

    def test_staff_nickname_signoff_passes_midsentence_stripped(self):
        self.assertTrue(self.t("Thanks,\n\nMari").endswith("Mari"))
        self.assertNotIn("Mari", self.t("I asked Mari about it yesterday."))

    def test_staff_full_name_still_passes(self):
        self.assertIn("Zorbleton Quimby", self.t("Zorbleton Quimby has the details."))


if __name__ == "__main__":
    unittest.main()
