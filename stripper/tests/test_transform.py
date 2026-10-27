"""Transform engine tests. Synthetic corpus and temp key stores only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import transform as T  # noqa: E402

DIRECTORY = G.parse_directory(C.directory_markdown())
TP = K.TestOnlyProtector()
PATIENT_CATS = {"nhs_number", "dob", "phone", "address", "postcode", "patient_email", "mrn"}


def make(td: str, name: str = "ks", year: int = 2026):
    store = K.KeyStore(Path(td) / name, TP)
    return store, T.Transformer(store, DIRECTORY, year=year)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.store, self.tr = make(self._td.name)


class UnitTests(Base):
    def t(self, s, hdr=()):
        return self.tr.text(s, hdr).text

    def test_patient_placeholders_are_fixed_and_carry_no_map(self):
        a = C.generate(1, 1)  # corpus module imported for value helpers below
        g = C.Gen(5)
        n1, n2 = g.nhs(), g.nhs()
        self.assertNotEqual(n1, n2)
        self.assertEqual(self.t(f"NHS {n1}."), self.t(f"NHS {n2}."))
        self.assertEqual(self.t(f"NHS {n1}."), "NHS [PT-NHS].")
        self.assertEqual(self.store.t["codes"]["REF"], {})
        self.assertEqual(set(self.store.t["codes"]), set(K.CODE_PREFIXES))
        self.assertTrue(a)

    def test_identifier_tokens(self):
        out = self.t("Re R26-AB12 and S26-CD34, WS26-12345, QC26-1234, case 2026/1234.")
        for pre in ("REF-", "SMP-", "WS-", "QC-", "CASE-"):
            self.assertIn(pre, out)
        self.assertNotIn("R26", out)
        self.assertEqual(out, self.t("Re R26-AB12 and S26-CD34, WS26-12345, QC26-1234, case 2026/1234."))

    def test_staff_and_crf_pass_through(self):
        out = self.t("Hi Callum,\nSee CRF-2026-019 and DOC6676 with Callum Ridgeway.")
        self.assertIn("CRF-2026-019", out)
        self.assertIn("DOC6676", out)

    def test_unknown_name_becomes_name_token_and_header_external_becomes_contact(self):
        # The NER layer is stubbed here (spaCy is not installed in CI); a fake finds the two names.
        def fake(text):
            return [T.D.Hit("NER_PERSON", "fail", i, i + len(n)) for n in ("Zed Quuxington", "Wren Fallow")
                    for i in [text.find(n)] if i >= 0]
        hdr = [("Zed Quuxington", "zed@example.org"), ("Pat Sender", "pat.sender@nhs.net")]
        self.tr.use_ner = True
        with mock.patch.object(T.N, "detect_ner", fake):
            out = self.t("Zed Quuxington wrote to zed@example.org and other@example.org about Wren Fallow.", hdr)
        self.assertEqual(out, "CONTACT-0001 wrote to CONTACT-0001 and [PT-EMAIL] about [NAME-1].")

    def test_clinical_escalations(self):
        one = self.t("The patient is aged 40 today.")
        self.assertIn("[CLIN]", one)
        self.assertIn("today.", one)                                  # word level only
        self.assertEqual(self.t("Carrier of c.76A>G. Next item."), "[CLIN] Next item.")  # multiple + result-style
        self.assertEqual(self.t("Aged 40 in R26-AB12 today. Fine."), "[CLIN] Fine.")      # next to an identifier
        self.assertEqual(self.t("Positive feedback received. Fine."), "[CLIN] Fine.")      # result-style (known false-strip)
        r = self.tr.text("Aged 40 and breast cancer here.")
        self.assertEqual(r.drops["multi"], 1)

    def test_dates(self):
        self.assertIn("[DATE]", self.t("Meeting on 12/03/2026."))
        self.assertIn("[PT-DOB]", self.t("DOB: 12/03/1980."))
        self.assertIn("[PT-DOB]", self.t("born 3 March."))

    def test_untreated_codes_are_left_and_counted(self):
        r = self.tr.text("See https://example.org/page for more.")
        self.assertIn("https://example.org/page", r.text)
        self.assertEqual(r.left["URL_EXTERNAL"], 1)

    def test_attachment_label_is_an_allowlist(self):
        out = self.tr.attachment("Report_Zed_Quux_R26-AB12_CRF-2026-019_v2.PDF", "h", 2)
        self.assertEqual(out, 'ATT-2 (pdf, "Report CRF-2026-019 v2")')
        self.assertEqual(self.tr.attachment("Zed Quux.bin;exe", "h", 3), "ATT-3 (file)")
        self.assertEqual(self.store.t["att"]["h"]["2"], "Report_Zed_Quux_R26-AB12_CRF-2026-019_v2.PDF")

    def test_tokens_do_not_trip_any_detector(self):
        for i in range(3000):
            for cat in K.CODE_PREFIXES:
                tok = self.store.token(cat, f"X{i}", 2026)
                _, hits = T.D.detect(f"See {tok} now")
                self.assertEqual(hits, [], tok)
        _, hits = T.D.detect("CONTACT-0001 ATT-3 (pdf) [NAME-12] [PT-NHS] [CLIN] [DATE]")
        self.assertEqual([h.code for h in hits], [])

    def test_ref_smp_round_trip_via_key_store(self):
        out = self.t("R26-AB12 and S26-CD34.")
        ref, smp = out.split(" and ")[0], out.split(" and ")[1].rstrip(".")
        self.store.save()
        s2 = K.KeyStore(self.store.dir, TP)
        self.assertEqual(s2.resolve(ref), "R26AB12")
        self.assertEqual(s2.resolve(smp), "S26CD34")

    def test_per_year_key_changes_tokens(self):
        _, tr27 = make(self._td.name, "ks27", 2027)
        # CASE numbers carry no year, so they take the processing year and must differ across years
        a = self.t("case 2026/1234")
        b = tr27.text("case 2026/1234").text
        self.assertNotEqual(a.split()[-1], b.split()[-1])
        # same store, same identifier, different key year
        self.assertNotEqual(self.store.token("REF", "R26AB12", 2026), self.store.token("REF", "R26AB12", 2027))


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.store, cls.tr = make(cls._td.name)
        cls.msgs = C.generate(1200, 20261027)
        cls.stats = T.run_corpus(cls.msgs, cls.tr)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_deterministic_output(self):
        for m in self.msgs[:200]:
            a = self.tr.message(m["fields"], m["meta"].get("header_people", []), commit=False, audit=False)
            self.assertEqual(a.fields, self.stats["outputs"][m["id"]].fields)

    def test_no_planted_value_survives(self):
        self.assertEqual(dict(self.stats["leaks"]), {})
        for cat in T.LEAK_CATS:
            self.assertGreater(self.stats["planted"][cat], 0, cat)

    def test_no_token_collisions_on_corpus(self):
        for cat, table in self.store.t["codes"].items():
            toks = list(table.values())
            self.assertGreater(len(toks), 0, cat)
            self.assertEqual(len(toks), len(set(toks)), cat)
        self.assertEqual(len(self.store._tok), sum(len(t) for t in self.store.t["codes"].values()))

    def test_patient_values_never_reach_the_store(self):
        blob = str(self.store.t)
        for m in self.msgs:
            for l in m["labels"]:
                if l["category"] in PATIENT_CATS and l["action"] == "strip":
                    v = m["fields"][l["field"]][l["start"]:l["end"]].strip()
                    self.assertNotIn(v, blob)
        self.assertEqual(set(self.store.t["codes"]), set(K.CODE_PREFIXES))

    def test_residual_preview_only_known_left_codes(self):
        fails = {k for k in self.stats["residual"] if k.endswith("/fail")}
        self.assertEqual(fails, set(), fails)
        self.assertEqual(set(self.stats["left"]) - {"DOB_KEYWORD", "URL_EXTERNAL"}, set())

    def test_output_contains_only_approved_tokens_and_no_raw_header_values(self):
        for r in self.stats["outputs"].values():
            for name, text in r.fields.items():
                if name.startswith("attachment_"):
                    self.assertRegex(text, r'^ATT-\d+ \([a-z0-9]+(, "[^"]*")?\)$')


if __name__ == "__main__":
    unittest.main()
