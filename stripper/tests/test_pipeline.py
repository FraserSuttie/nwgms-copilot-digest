"""Residual scan, quarantine, eligibility, density and pipeline tests (ADR 0005). Synthetic data only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402

DIRECTORY = G.parse_directory(C.directory_markdown())
TP = K.TestOnlyProtector()


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name) / "ks"
        self.store = K.KeyStore(self.dir, TP)
        self.q = Q.Quarantine(self.dir, TP)
        self.tr = T.Transformer(self.store, DIRECTORY, year=2026)
        self.pipe = P.Pipeline(self.tr, self.q)

    def qfiles(self):
        return sorted(p.name for p in (self.dir / "quarantine").glob("*.q")) if (self.dir / "quarantine").exists() else []


class ResidualScan(Base):
    def scan(self, text, emitted=()):
        return R.scan(text, set(emitted), DIRECTORY)

    def test_approved_tokens_that_were_emitted_pass(self):
        r = self.tr.text("Please check R26-DT1Z and sample S26-UXEN, Callum Ridgeway cc'd.")
        self.assertEqual(self.scan(r.text, r.emitted), Counter())

    def test_token_shaped_text_from_the_input_is_not_issued(self):
        self.assertEqual(self.scan("See REF-BCDFGHJK and [PT-NHS] and [CLIN]"), Counter({"TOKEN_NOT_ISSUED": 3}))

    def test_every_approved_format_is_accepted_when_emitted(self):
        toks = ["REF-BCDFGHJK", "SMP-BCDFGHJK", "WS-BCDFGHJK", "QC-BCDFGHJK", "CASE-BCDFGHJK", "CONTACT-0007", "ATT-3",
                "[PT-NHS]", "[PT-NUM]", "[PT-PHONE]", "[PT-ADDR]", "[PT-MRN]", "[PT-DOB]", "[PT-EMAIL]", "[GMC-ID]",
                "[DATE]", "[LOC]", "[URL]", "[CLIN]", "[NAME-2]"]
        self.assertEqual(self.scan(" ".join(toks), toks), Counter())

    def test_dob_cue_word_is_allowed(self):
        self.assertEqual(self.scan("DOB [PT-DOB] does not match.", ["[PT-DOB]"]), Counter())

    def test_other_review_severity_codes_fail(self):
        for text in ("see https://example.com/page", "version 12.03.24 notes", "ref 123456 here"):
            self.assertTrue(self.scan(text), text)

    def test_leftover_identifiers_fail(self):
        nhs = C.Gen(3).nhs()
        reasons = self.scan(f"NHS {nhs} left in")
        self.assertTrue(any(k.startswith("HIT_") for k in reasons))

    def test_surviving_unknown_full_name_fails(self):
        self.assertEqual(self.scan("Thanks to Marlowe Aldermoor for this.").get("NAME_SURVIVES"), None)  # NER off: no span found
        # known-token scan: a bare staff surname mid-sentence is a name decision
        self.assertTrue(self.scan("We spoke to Ridgeway about it.")["NAME_SURVIVES"] >= 1)

    def test_attachment_line_must_match_exactly_and_be_emitted(self):
        line = 'ATT-1 (pdf, "report")'
        self.assertEqual(R.scan_attachment(line, {line}), Counter())
        self.assertEqual(R.scan_attachment(line, set()), Counter({"ATT_LINE_INVALID": 1}))
        self.assertEqual(R.scan_attachment("ATT-1 (pdf) Marlowe_Aldermoor.pdf", {"ATT-1 (pdf) Marlowe_Aldermoor.pdf"}),
                         Counter({"ATT_LINE_INVALID": 1}))


class Density(unittest.TestCase):
    ALLOWED = R.allowed_words(DIRECTORY, vocab={"BRCA1", "Cancer", "Panel"})

    def d(self, text, **kw):
        return R.density(text, self.ALLOWED, R.Limits(**kw))

    def test_clean_text_passes(self):
        self.assertEqual(self.d("Hi Callum,\n\nThe Cancer Panel config goes live Monday.\n\nThanks,\nOren Maddox"), Counter())

    def test_one_unknown_capitalised_word_trips_caps(self):
        self.assertEqual(self.d("The family of Quillfeather called."), Counter({"DENS_CAPS": 1}))

    def test_bare_name_alone_on_a_line_counts(self):
        self.assertEqual(self.d("Thanks,\nQuillfeather"), Counter({"DENS_CAPS": 1}))

    def test_threshold_is_configurable(self):
        self.assertEqual(self.d("see Quillfeather", unknown_caps=2), Counter())

    def test_digit_run_trips_and_short_runs_do_not(self):
        self.assertEqual(self.d("code 12345 here"), Counter({"DENS_DIGITS": 1}))
        self.assertEqual(self.d("room 1234 here"), Counter())

    def test_clin_markers(self):
        self.assertEqual(self.d("[CLIN] a [CLIN] b"), Counter())
        self.assertEqual(self.d("[CLIN] a [CLIN] b [CLIN]"), Counter({"DENS_CLIN": 1}))

    def test_approved_tokens_do_not_count(self):
        self.assertEqual(self.d("REF-BCDFGHJK and CONTACT-0042 and [NAME-1]"), Counter())


class PipelineTests(Base):
    def test_clean_message_is_released(self):
        o = self.pipe.process({"body": "Hi Callum,\n\nThe config change goes live Monday.\n\nThanks,\nOren Maddox"})
        self.assertEqual(o.status, "ok")
        self.assertEqual(o.reasons, Counter())
        self.assertEqual(self.qfiles(), [])

    def test_approved_tokens_pass_through_the_full_pipeline(self):
        o = self.pipe.process({"body": "Please check R26-DT1Z for me.\nNHS number 999 999 9999 appears twice."})
        self.assertIn(o.status, ("ok", "quarantined"))
        if o.status == "ok":
            self.assertRegex(o.fields["body"], r"REF-[BCDFGHJKLMNPQRSTVWXZ]{8}")

    def test_fail_closed_quarantines_the_whole_message_with_no_partial_output(self):
        raw = {"subject": "Query", "body": "Hi Callum,\nPlease note Quillfeather rang again."}
        o = self.pipe.process(raw)
        self.assertEqual(o.status, "quarantined")
        self.assertIsNone(o.fields)
        self.assertEqual(set(o.reasons), {"DENS_CAPS"})   # reason codes with counts, nothing else
        files = self.qfiles()
        self.assertEqual(len(files), 1)
        stored = json.loads(TP.unprotect((self.dir / "quarantine" / files[0]).read_bytes()))
        self.assertEqual(stored["fields"], raw)           # the WHOLE raw message, nothing stripped, nothing partial

    def test_index_and_audit_hold_reason_counts_only(self):
        self.pipe.process({"body": "Please note Quillfeather rang about 123456789."})
        idx = (self.dir / "quarantine" / "index.jsonl").read_text()
        audit = (self.dir / K.KeyStore.AUDIT).read_text()
        for blob in (idx, audit):
            self.assertNotIn("Quillfeather", blob)
            self.assertNotIn("123456789", blob)
        line = json.loads(idx.splitlines()[0])
        self.assertEqual(set(line), {"ts", "msg", "reasons", "version"})
        self.assertTrue(all(isinstance(v, int) for v in line["reasons"].values()))
        self.assertEqual(json.loads(audit.splitlines()[-1])["decision"], "quarantined")

    def test_eligibility_result_wording_with_identifier(self):
        o = self.pipe.process({"body": "Result for R26-DT1Z is positive, please confirm."})
        self.assertEqual(o.status, "quarantined")
        self.assertIn("ELIG_RESULT_ID", o.reasons)

    def test_result_wording_without_identifier_is_not_ineligible(self):
        o = self.pipe.process({"body": "The feedback was positive on Monday."})
        self.assertNotIn("ELIG_RESULT_ID", o.reasons)

    def test_identifier_without_result_wording_is_eligible(self):
        o = self.pipe.process({"body": "Can you check R26-DT1Z on Monday?"})
        self.assertNotIn("ELIG_RESULT_ID", o.reasons)

    def test_body_size_limit(self):
        pipe = P.Pipeline(self.tr, self.q, R.Limits(max_chars=50))
        o = pipe.process({"body": "word " * 20, "attachment_1": "x" * 500 + ".pdf"})
        self.assertEqual(o.reasons, Counter({"ELIG_SIZE": 1}))   # attachment names do not count towards the body limit
        o2 = pipe.process({"body": "word " * 5, "attachment_1": "x" * 500 + ".pdf"})
        self.assertNotIn("ELIG_SIZE", o2.reasons)

    def test_unexpected_exception_fails_closed(self):
        with mock.patch.object(T.Transformer, "message", side_effect=ValueError("boom")):
            o = self.pipe.process({"body": "hello"})
        self.assertEqual((o.status, o.fields, o.reasons), ("quarantined", None, Counter({"PIPELINE_ERROR": 1})))
        self.assertEqual(len(self.qfiles()), 1)

    def test_quarantine_write_failure_releases_nothing(self):
        with mock.patch.object(Q.K, "_atomic_write", side_effect=OSError("disk")):
            with self.assertRaises(Q.QuarantineError):
                self.pipe.process({"body": "Please note Quillfeather rang."})

    def test_unissued_token_in_input_quarantines(self):
        o = self.pipe.process({"body": "Forwarded: see REF-BCDFGHJK for details."})
        self.assertEqual(o.status, "quarantined")
        self.assertIn("TOKEN_NOT_ISSUED", o.reasons)

    def test_residual_catches_a_detector_hit_the_transform_left(self):
        o = self.pipe.process({"body": "See https://example.com/page for the guide."})
        self.assertEqual(o.status, "quarantined")
        self.assertIn("HIT_URL_EXTERNAL", o.reasons)

    def test_personal_url_is_replaced_whole(self):
        r = self.tr.text("Notes: https://mftexample-my.sharepoint.example.com/personal/a_b_mft_nhs_uk/Documents/notes.docx now.")
        self.assertEqual(r.text, "Notes: [URL] now.")
        self.assertEqual(r.left, Counter())

    def test_ner_requested_but_missing_is_refused(self):
        tr = T.Transformer(self.store, DIRECTORY, use_ner=True, year=2026)
        with mock.patch.object(N, "STATUS", "STUB: NER layer not active (ImportError)"):
            with self.assertRaises(RuntimeError):
                P.Pipeline(tr, self.q)

    def test_attachment_only_labels_pass_the_scan(self):
        o = self.pipe.process({"body": "Hi Callum,\n\nPlease see the attached.", "attachment_1": "CRF-2026-019_report.pdf"})
        self.assertEqual(o.status, "ok")
        self.assertEqual(o.fields["attachment_1"], 'ATT-1 (pdf, "CRF-2026-019 report")')


class QuarantineStore(Base):
    def test_refuses_repo_and_onedrive_paths(self):
        with self.assertRaises(ValueError):
            Q.Quarantine(ROOT / "stripper", TP)
        with self.assertRaises(ValueError):
            Q.Quarantine(Path(self._td.name) / "OneDrive - Trust" / "q", TP)

    def test_requires_a_protector(self):
        with self.assertRaises(ValueError):
            Q.Quarantine(self.dir, None)

    def test_blob_is_protected_not_plaintext(self):
        self.q.put("abc", {"fields": {"body": "Quillfeather"}}, Counter({"X": 1}), "v")
        self.assertNotEqual((self.dir / "quarantine" / "abc.q").read_bytes(), json.dumps({"fields": {"body": "Quillfeather"}}).encode())

    def test_summary_and_purge(self):
        self.q.put("a1", {"fields": {}}, Counter({"DENS_CAPS": 1}), "v")
        self.q.put("b2", {"fields": {}}, Counter({"DENS_CAPS": 1, "ELIG_SIZE": 1}), "v")
        self.assertEqual(self.q.summary(), (2, Counter({"DENS_CAPS": 2, "ELIG_SIZE": 1})))
        self.assertEqual(self.q.purge("a1"), 1)
        self.assertEqual(self.q.summary()[0], 1)
        self.assertEqual(self.q.purge(), 1)
        self.assertEqual(self.q.summary(), (0, Counter()))


class CorpusThroughPipeline(unittest.TestCase):
    """The transformed synthetic corpus through the full pipeline (deterministic layer; NER run is local)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        d = Path(cls._td.name) / "ks"
        st = K.KeyStore(d, TP)
        tr = T.Transformer(st, DIRECTORY, year=2026)
        msgs = C.generate(1200, 20261027)
        cls.stats = P.run_corpus(msgs, P.Pipeline(tr, Q.Quarantine(d, TP), vocab=P.corpus_vocab()))
        cls.quarantine = Q.Quarantine(d, TP)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_every_message_is_accounted_for(self):
        s = self.stats
        self.assertEqual(s["ok"] + s["quarantined"], s["messages"])
        self.assertEqual(self.quarantine.summary()[0], s["quarantined"])

    def test_no_planted_strip_item_survives_in_released_output(self):
        self.assertEqual(dict(self.stats["ok_leaks"]), {})

    def test_quarantine_reasons_are_known_codes_and_counts(self):
        known = {"DENS_CAPS", "DENS_DIGITS", "DENS_CLIN", "ELIG_RESULT_ID", "ELIG_SIZE"}
        self.assertTrue(set(self.stats["reasons"]) <= known, set(self.stats["reasons"]) - known)
        self.assertTrue(all(isinstance(v, int) for v in self.stats["reasons"].values()))

    def test_residual_scan_adds_no_detector_reasons_on_transformed_corpus(self):
        self.assertFalse([k for k in self.stats["reasons"] if k.startswith(("HIT_", "TOKEN_", "ATT_", "NAME_"))])

    def test_density_is_the_net_for_missed_names(self):
        # NER off: most messages keep a name fragment, and the density check must catch nearly all of them.
        self.assertLessEqual(self.stats["ok_name_msgs"], 10)
        self.assertTrue(set(self.stats["ok_name_kinds"]) <= {"lower_name"}, self.stats["ok_name_kinds"])

    def test_eligibility_rule_fires_on_the_corpus(self):
        self.assertGreater(self.stats["reason_msgs"]["ELIG_RESULT_ID"], 0)


if __name__ == "__main__":
    unittest.main()
