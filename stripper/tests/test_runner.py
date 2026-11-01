"""Batch runner tests: paths, dedup, thread key, append-only output, manifest, counts-only report, full synthetic
email corpus (ADR 0006). Synthetic data only, temp directories only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import rawdrop as RD  # noqa: E402
import review as RV  # noqa: E402
import runner as RN  # noqa: E402
import namegate as G  # noqa: E402
from test_mailfront import raw  # noqa: E402

TP = K.TestOnlyProtector()
NOW = datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.drop, self.state, self.out = self.td / "drop", self.td / "state", self.td / "out"
        self.drop.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def put(self, name: str, obj) -> None:
        (self.drop / name).write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def run_(self, **kw) -> RN.Report:
        return RN.run(self.drop, self.state, self.out, C.directory_markdown(), vocab=P.corpus_vocab(),
                      protector=TP, use_ner=False, year=2026, now=NOW, **kw)

    def threads(self) -> list[Path]:
        return sorted((self.out / "Comms" / "_Messages").glob("THR-*.md"))


class Paths(Base):
    def test_refusals(self):
        repo = G.REPO_ROOT
        cases = [dict(drop=repo / "x"), dict(state=repo / "x"), dict(out=repo / "x"),
                 dict(state=self.td / "OneDrive - Trust" / "s"), dict(out=self.drop / "o"), dict(state=self.drop)]
        for c in cases:
            a = {"drop": self.drop, "state": self.state, "out": self.out, **c}
            with self.assertRaises(RN.Refused):
                RN.check_paths(a["drop"], a["state"], a["out"])
        for f in (repo / "vocab.txt", self.td / "OneDrive" / "v.txt"):
            with self.assertRaises(RN.Refused):
                RN.check_paths(self.drop, self.state, self.out, vocab=f)

    def test_drop_and_out_may_be_onedrive(self):
        od = self.td / "OneDrive - Trust"
        RN.check_paths(od / "drop", self.state, od / "out")

    def test_vault_guard(self):
        vault = self.td / "vault"
        vault.mkdir()
        (vault / "CLAUDE.md").write_text("# x\n\n## 8. Comms lane (stripped working communications)\n", encoding="utf-8")
        with self.assertRaises(RN.Refused):
            RN.check_paths(self.drop, self.state, vault)
        with self.assertRaises(RN.Refused):
            RN.check_paths(vault / "drop", self.state, self.out, lane_active=True)
        RN.check_paths(self.drop, self.state, vault, lane_active=True)


class Runs(Base):
    def test_release_block_shape(self):
        self.put("a.json", raw())
        rep = self.run_()
        self.assertEqual((rep.released, rep.quarantined, rep.threads_new), (1, 0, 1))
        [t] = self.threads()
        self.assertRegex(t.name, r"^THR-[BCDFGHJKLMNPQRSTVWXZ]{8}\.md$")
        text = t.read_text(encoding="utf-8")
        self.assertIn("thread: " + t.stem, text)
        self.assertIn("- from: Ada Fairweather | to: 1 (Callum Ridgeway) | cc: 0 (none) | reply-to: none", text)
        self.assertIn("**Subject:** Panel question", text)
        self.assertEqual(RN.torn_blocks(text), 0)
        manifest = list((self.out / "_Intake").glob("comms-batch-*.md"))
        self.assertEqual(len(manifest), 1)
        self.assertIn(f"[[{t.stem}]]: 1 new block(s)", manifest[0].read_text(encoding="utf-8"))

    def test_dedup_within_and_across_runs(self):
        self.put("a.json", raw())
        self.put("a-copy.json", raw(internet_message_id="<other@example.invalid>"))   # same content, other id
        rep = self.run_()
        self.assertEqual((rep.released, rep.duplicates), (1, 1))
        rep2 = self.run_()
        self.assertEqual((rep2.released, rep2.duplicates), (0, 2))
        self.assertEqual(self.threads()[0].read_text(encoding="utf-8").count("<!-- block:"), 1)

    def test_thread_key_stable_and_append_only(self):
        self.put("a.json", raw())
        self.run_()
        [t] = self.threads()
        before = t.read_bytes()
        self.put("b.json", raw(subject="RE: Panel question", sent="2026-10-30T10:00:00Z"))
        self.put("c.json", raw(conversation_id="synthetic-conversation-2", sent="2026-10-30T11:00:00Z"))
        rep = self.run_()
        self.assertEqual((rep.released, rep.threads_new), (2, 1))
        self.assertEqual(len(self.threads()), 2)
        self.assertTrue(t.read_bytes().startswith(before))
        self.assertEqual(t.read_text(encoding="utf-8").count("<!-- /block:"), 2)
        st = K.KeyStore(self.state, TP)
        self.assertEqual(st.thread_key("synthetic-conversation-1"), t.stem)
        self.assertEqual(st.thread_key(" synthetic-conversation-1 "), t.stem)
        self.assertNotEqual(st.thread_key("synthetic-conversation-2"), t.stem)

    def test_replay_after_crash_does_not_duplicate(self):
        self.put("a.json", raw())
        self.run_()
        (self.state / RN.SEEN).unlink()          # crash between append and the seen record
        rep = self.run_()
        self.assertEqual((rep.already_written, rep.released), (1, 1))
        self.assertEqual(self.threads()[0].read_text(encoding="utf-8").count("<!-- block:"), 1)

    def test_torn_block_reported(self):
        self.put("a.json", raw())
        self.run_()
        with open(self.threads()[0], "a", encoding="utf-8") as fh:
            fh.write("<!-- block:deadbeef -->\npartial")
        self.assertEqual(self.run_().torn_blocks, 1)

    def test_rejects_and_unreadable_counted_left_in_drop(self):
        self.put("bad.json", raw(extra="x"))
        self.put("junk.json", "{not json")
        (self.drop / "enc.json").write_bytes(b"\xff\xfe\x00bad")
        rep = self.run_()
        self.assertEqual((rep.schema_rejects, rep.unreadable, rep.released, rep.quarantined), (1, 2, 0, 0))
        self.assertEqual(len(list(self.drop.glob("*.json"))), 3)

    def test_quarantine_and_retry(self):
        body = "Hi Callum,\n\nZebedee Quolworth rang.\n\nThanks,\nAda"
        self.put("q.json", raw(body={"content_type": "text", "content": body}))
        rep = self.run_()
        self.assertEqual((rep.quarantined, rep.reasons["DENS_CAPS"]), (1, 1))
        self.assertEqual(self.threads(), [])
        self.assertEqual(self.run_().duplicates, 1)
        rep3 = RN.run(self.drop, self.state, self.out, C.directory_markdown(), vocab=P.corpus_vocab(),
                      ignore_words=frozenset({"zebedee", "quolworth"}), retry_quarantined=True,
                      protector=TP, use_ner=False, year=2026, now=NOW)
        self.assertEqual(rep3.released, 1)

    def test_external_header_name_in_body_not_allowed(self):
        cc = [{"name": "Rosalind Wetherbourne", "address": "r.w@example.org"}]
        body = "Hi Callum,\n\nSupplier contact Rosalind Wetherbourne will call.\n\nThanks,\nAda"
        self.put("x.json", raw(cc=cc, body={"content_type": "text", "content": body}))
        rep = self.run_()
        out = "".join(p.read_text(encoding="utf-8") for p in self.threads())
        self.assertNotIn("Wetherbourne", out)
        self.assertNotIn("r.w@example.org", out)
        self.assertEqual(rep.released + rep.quarantined, 1)

    def test_front_error_quarantines_whole_raw(self):
        self.put("a.json", raw())
        with mock.patch.object(RN.M, "build", side_effect=RuntimeError("boom")):
            rep = self.run_()
        self.assertEqual((rep.quarantined, rep.reasons["FRONT_ERROR"]), (1, 1))
        self.assertEqual(self.threads(), [])

    def test_quarantine_write_failure_stops_run(self):
        body = "Hi Callum,\n\nZebedee Quolworth rang.\n\nThanks,\nAda"
        self.put("a.json", raw(body={"content_type": "text", "content": body}))
        self.put("b.json", raw(sent="2026-10-30T12:00:00Z"))
        with mock.patch.object(Q.Quarantine, "put", side_effect=Q.QuarantineError("x")):
            rep = self.run_()
        self.assertTrue(rep.stopped)
        self.assertEqual(rep.released, 0)
        self.assertEqual(self.threads(), [])


class Report(Base):
    def test_counts_only(self):
        secret = "Zebedee Quolworth"
        self.put("q.json", raw(body={"content_type": "text", "content": f"Hi Callum,\n\n{secret} rang.\n\nThanks"}))
        self.put("bad.json", raw(subject=5))
        rep = self.run_()
        buf = io.StringIO()
        RN.print_report(rep, buf)
        text = buf.getvalue() + "".join(p.read_text(encoding="utf-8") for p in (self.out / "_Intake").glob("*.md"))
        for v in ("Zebedee", "Quolworth", "Callum", "Ada", "synthetic-conversation"):
            self.assertNotIn(v, text)
        self.assertIn("quarantined: 1", text)

    def test_cli_suppresses_exception_text_and_requires_ner(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()), \
                mock.patch.object(RN.N, "load"), mock.patch.object(RN.N, "STATUS", "stub (no presidio)"):
            code = RN.main(["--drop", str(self.drop), "--state", str(self.state), "--out", str(self.out),
                            "--directory", str(self.td / "missing.md")])
        self.assertNotEqual(code, 0)
        self.assertNotIn("missing.md", err.getvalue())


class ReviewCovered(unittest.TestCase):
    def test_list_hides_covered_names(self):
        td = Path(tempfile.mkdtemp())
        try:
            st = G.State(td / "s", TP)
            st.merge_counts({"zebedee quolworth": 3, "status": 4})
            (td / "ignore.txt").write_text("Status\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                RV.cmd_list(st, 1, RV.covered_words(ignore=td / "ignore.txt"))
            self.assertIn("zebedee", buf.getvalue())
            self.assertNotIn("status", buf.getvalue().split("--")[0])
        finally:
            shutil.rmtree(td, ignore_errors=True)


class Corpus(unittest.TestCase):
    """Full synthetic email corpus through the runner (NER off here; the NER-on run is measured locally)."""

    def test_corpus_end_to_end(self):
        td = Path(tempfile.mkdtemp())
        try:
            info = RD.write(td / "rd", 400, 20261027)
            rep = RN.run(td / "rd" / "drop", td / "state", td / "out", (td / "rd" / "directory.md").read_text(encoding="utf-8"),
                         vocab=P.corpus_vocab(), protector=TP, use_ner=False, year=2026, now=NOW)
            self.assertEqual(rep.files, 400 + info["duplicate_files"])
            self.assertEqual(rep.duplicates, info["duplicate_files"])
            self.assertEqual(rep.released + rep.quarantined, 400)
            self.assertEqual((rep.schema_rejects, rep.unreadable, rep.torn_blocks), (0, 0, 0))
            s = RD.score(td / "rd", td / "out")
            self.assertEqual(s["released blocks"], rep.released)
            self.assertEqual(s["planted strip values present"], {})
            self.assertEqual(s["history plants present"], 0)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
