"""Tests for the digest validator. Run from the repo root:

    python -m unittest discover -s validator/tests -v

Standard library only. All data is synthetic (see generators.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import generators as G  # noqa: E402
import patterns as P  # noqa: E402
import validate_digest as V  # noqa: E402

FIX = HERE / "fixtures"
CLI = HERE.parent / "validate_digest.py"


def load(path: Path, fill: bool = False) -> bytes:
    text = path.read_text(encoding="utf-8")
    return (G.fill(text) if fill else text).encode("utf-8")


def codes(findings, where_prefix: str | None = None) -> set[str]:
    return {f.code for f in findings if where_prefix is None or f.where.startswith(where_prefix)}


def digest(**entry_overrides) -> dict:
    entry = {
        "source": "email", "received": "2026-10-26T06:58", "counterpart": "Sam Sample, Test Analyst",
        "systems": ["iGene"], "request_type": "FYI", "action_needed": "none", "direction": "owed-by-me",
    }
    entry.update(entry_overrides)
    return {"schema_version": "1", "generated": "2026-10-26T07:30:00Z", "lane": "A", "entries": [entry]}


def run_doc(doc: dict, **kw):
    return V.validate(json.dumps(doc).encode("utf-8"), **kw)


class NhsChecksum(unittest.TestCase):
    def test_generated_number_is_valid(self):
        self.assertTrue(P.nhs_checksum_ok(G.nhs_digits()))

    def test_flipped_check_digit_is_invalid(self):
        self.assertFalse(P.nhs_checksum_ok(G.nhs_bad_digits()))

    def test_wrong_length_or_non_digit(self):
        self.assertFalse(P.nhs_checksum_ok("123"))
        self.assertFalse(P.nhs_checksum_ok("99912345AB"))


class CleanFixtures(unittest.TestCase):
    def test_every_clean_fixture_passes(self):
        files = sorted((FIX / "clean").glob("*.json"))
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            with self.subTest(f=f.name):
                self.assertEqual(V.validate(load(f)), [])


class LeakClasses(unittest.TestCase):
    """Each synthetic leak, placed in the topic field, must be caught."""

    CASES = {
        "NHS_VALID": "NHS_NUMBER", "NHS_SPACED": "NHS_NUMBER", "NHS_BAD": "TEN_DIGITS",
        "PHONE_MOBILE": "PHONE_UK", "PHONE_LANDLINE": "PHONE_UK", "PHONE_LONDON": "PHONE_UK",
        "PHONE_INTL": "PHONE_UK", "PHONE_COMPACT": "PHONE_UK",
        "DOB_SLASH": "DATE", "DOB_ISO": "DATE", "DOB_TEXT": "DATE", "DOB_US_TEXT": "DATE",
        "POSTCODE": "POSTCODE", "EXT_EMAIL": "EMAIL_EXTERNAL",
        "SAMPLE_ID": "IGENE_SAMPLE", "DERIVED_ID": "IGENE_SAMPLE", "REFERRAL_ID": "IGENE_REFERRAL",
        "WORKSHEET_ID": "IGENE_ID_WORKSHEET", "QC_ID": "IGENE_ID_QC", "CASE_ID": "IGENE_CASE",
        "PATIENT_ID": "PATIENT_NUMBER", "PATIENT_ID_LARGE": "PATIENT_NUMBER",
    }

    def test_each_class_is_caught_in_topic(self):
        for token, expected in self.CASES.items():
            with self.subTest(token=token):
                value = G.TOKENS[token]()
                found = run_doc(digest(topic=f"About {value}"))
                self.assertIn(expected, codes(found, "entries[0].topic"), token)

    def test_each_class_is_caught_in_counterpart(self):
        for token, expected in self.CASES.items():
            with self.subTest(token=token):
                value = G.TOKENS[token]()
                found = run_doc(digest(counterpart=f"Alex Example {value}"))
                self.assertIn(expected, codes(found, "entries[0].counterpart"), token)

    def test_leaky_template_catches_each_planted_leak(self):
        found = V.validate(load(FIX / "leaky" / "leaky_template.json", fill=True))
        expected = {
            "entries[0].counterpart": {"PHONE_UK", "SCHEMA_COUNTERPART"},
            "entries[1].topic": {"IGENE_SAMPLE"},
            "entries[2].refs": {"SCHEMA_REF_NOT_ALLOWED", "IGENE_REFERRAL"},
            "entries[3].topic": {"NHS_NUMBER"},
            "entries[4]": {"SCHEMA_UNKNOWN_KEY"},
            "entries[5].topic": {"PATIENT_NUMBER"},
            "entries[6].systems": {"SCHEMA_SYSTEMS", "POSTCODE"},
            "entries[7].counterpart": {"SCHEMA_COUNTERPART", "EMAIL_EXTERNAL"},
        }
        got = {(f.where, f.code) for f in found}
        for where, want in expected.items():
            for code in want:
                self.assertIn((where, code), got, f"{where} {code}")

    def test_leaky_template_fails_on_the_cli_and_never_echoes_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "leaky.json"
            p.write_bytes(load(FIX / "leaky" / "leaky_template.json", fill=True))
            r = subprocess.run([sys.executable, "-I", str(CLI), str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            out = r.stdout + r.stderr
            for token in ("NHS_VALID", "PHONE_MOBILE", "SAMPLE_ID", "REFERRAL_ID", "EXT_EMAIL", "POSTCODE"):
                self.assertNotIn(G.TOKENS[token](), out, f"value for {token} was echoed")
            self.assertIn("RESULT: FAIL", out)


class Benign(unittest.TestCase):
    def test_allowed_shapes_do_not_trip_the_scanner(self):
        for text in ("CRF-2026-903", "IG-10002", "#10001", "DOC9002", "iGene v4.07.0", "Q4 planning",
                     "Release testing", "Pat Dobson, Biomedical Scientist", "Zoë O'Brien-Smith"):
            with self.subTest(text=text):
                self.assertEqual(P.scan_text(text), [])

    def test_valid_email_domains_are_not_flagged_by_the_email_rule(self):
        self.assertEqual([h for h in P.scan_text("pat@mft.nhs.uk and lee@nhs.net") if h.code == "EMAIL_EXTERNAL"], [])
        self.assertTrue([h for h in P.scan_text("pat@mft.nhs.uk.evil.example") if h.code == "EMAIL_EXTERNAL"])


class Borderline(unittest.TestCase):
    def test_valid_refs_and_names_pass(self):
        for name in ("ref_edge_valid.json", "name_edge_valid.json"):
            with self.subTest(name=name):
                self.assertEqual(V.validate(load(FIX / "borderline" / name)), [])

    def test_ambiguous_cases_are_review_not_fail(self):
        expect = {"six_digit_review.json": "SIX_DIGITS", "version_dotted_review.json": "DATE_DOTTED",
                  "dob_keyword_review.json": "DOB_KEYWORD"}
        for name, code in expect.items():
            with self.subTest(name=name):
                found = V.validate(load(FIX / "borderline" / name))
                self.assertEqual({(f.code, f.severity) for f in found}, {(code, "review")})
                self.assertEqual(V.exit_code(found), 1)               # strict blocks (Phase 1)
                self.assertEqual(V.exit_code(found, lenient=True), 0)  # lenient is a tuning aid

    def test_lenient_still_blocks_fail_severity(self):
        found = run_doc(digest(topic=G.TOKENS["SAMPLE_ID"]()))
        self.assertEqual(V.exit_code(found, lenient=True), 1)

    def test_long_digit_runs_are_held_for_review(self):
        for value in ("1234567890123", "20261026"):
            with self.subTest(value=value):
                found = run_doc(digest(topic=f"batch {value}"))
                self.assertIn(("LONG_DIGITS", "review"), {(f.code, f.severity) for f in found})

    def test_month_year_is_review(self):
        found = run_doc(digest(topic="March 1984"))
        self.assertIn(("MONTH_YEAR", "review"), {(f.code, f.severity) for f in found})

    def test_received_and_due_dates_are_allowed_but_only_in_strict_shape(self):
        self.assertEqual(run_doc(digest(due="2026-11-02")), [])
        self.assertIn("SCHEMA_DUE", codes(run_doc(digest(due="02/11/2026"))))
        self.assertIn("SCHEMA_RECEIVED", codes(run_doc(digest(received="26 October 2026"))))


class Structure(unittest.TestCase):
    def test_invalid_json(self):
        self.assertEqual(codes(V.validate(b"{not json")), {"INVALID_JSON"})

    def test_duplicate_keys_are_rejected(self):
        raw = b'{"schema_version":"1","schema_version":"1","lane":"A","generated":"2026-10-26T07:30:00Z","entries":[]}'
        self.assertEqual(codes(V.validate(raw)), {"DUPLICATE_KEY"})

    def test_non_utf8_and_oversize(self):
        self.assertEqual(codes(V.validate(b"\xff\xfe\x00")), {"FILE_ENCODING"})
        self.assertEqual(codes(V.validate(b" " * (V.MAX_BYTES + 1))), {"FILE_TOO_LARGE"})

    def test_unknown_top_level_key(self):
        doc = digest()
        doc["note"] = "free text"
        self.assertIn("SCHEMA_UNKNOWN_KEY", codes(run_doc(doc)))

    def test_missing_required_field(self):
        doc = digest()
        del doc["entries"][0]["request_type"]
        self.assertIn("SCHEMA_MISSING", codes(run_doc(doc)))

    def test_entry_cap(self):
        doc = digest()
        doc["entries"] = doc["entries"] * (V.MAX_ENTRIES + 1)
        self.assertIn("TOO_MANY_ENTRIES", codes(run_doc(doc)))

    def test_lane_rules(self):
        self.assertIn("SCHEMA_VALUE", codes(run_doc(digest(source="teams-chat"))))      # lane A is email only
        self.assertIn("SCHEMA_VALUE", codes(run_doc(digest(direction="fyi"))))          # lane A is owed-by-me only
        doc = digest(source="meeting-recap", direction="fyi")
        doc["lane"] = "B4"
        self.assertEqual(run_doc(doc), [])

    def test_fixed_lists(self):
        self.assertIn("SCHEMA_VALUE", codes(run_doc(digest(request_type="Free text here"))))
        self.assertIn("SCHEMA_VALUE", codes(run_doc(digest(action_needed="escalate"))))
        self.assertIn("SCHEMA_SYSTEMS", codes(run_doc(digest(systems=["Excel"]))))
        self.assertIn("SCHEMA_SYSTEMS", codes(run_doc(digest(systems=[]))))

    def test_refs_must_match_an_allowed_shape(self):
        self.assertIn("SCHEMA_REF_NOT_ALLOWED", codes(run_doc(digest(refs=["TICKET-1"]))))
        self.assertIn("SCHEMA_REF_NOT_ALLOWED", codes(run_doc(digest(refs=["#1234567"]))))
        self.assertIn("SCHEMA_REFS", codes(run_doc(digest(refs=["#10001"] * 6))))

    def test_counterpart_rejects_digits_and_symbols(self):
        for bad in ("Alex 2", "alex@example.com", "x" * 61, ""):
            with self.subTest(bad=bad):
                self.assertIn("SCHEMA_COUNTERPART", codes(run_doc(digest(counterpart=bad))))

    def test_hidden_characters_and_width_tricks(self):
        self.assertIn("CONTROL_CHAR", codes(run_doc(digest(topic="Rel​ease"))))
        fullwidth = "".join(chr(0xFF10 + int(c)) for c in "1234567")
        self.assertIn("PATIENT_NUMBER", codes(run_doc(digest(topic=fullwidth))))

    def test_known_topic_and_people_lists(self):
        found = run_doc(digest(topic="Release testing"), topics={"Other topic"}, people={"Sam Sample"})
        self.assertEqual(codes(found), {"TOPIC_UNKNOWN"})
        found = run_doc(digest(), people={"Someone Else"})
        self.assertEqual(codes(found), {"COUNTERPART_UNKNOWN"})
        self.assertEqual(run_doc(digest(topic="Release testing"), topics={"Release testing"}, people={"Sam Sample"}), [])


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(CLI), *args], capture_output=True, text=True)

    def test_exit_codes(self):
        self.assertEqual(self.run_cli(str(FIX / "clean" / "lane_a_basic.json")).returncode, 0)
        self.assertEqual(self.run_cli(str(FIX / "borderline" / "six_digit_review.json")).returncode, 1)
        self.assertEqual(self.run_cli(str(FIX / "borderline" / "six_digit_review.json"), "--lenient").returncode, 0)
        self.assertEqual(self.run_cli(str(FIX / "does_not_exist.json")).returncode, 2)

    def test_json_output(self):
        r = self.run_cli(str(FIX / "borderline" / "dob_keyword_review.json"), "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["result"], "FAIL")
        self.assertEqual(data["findings"][0]["code"], "DOB_KEYWORD")


if __name__ == "__main__":
    unittest.main()
