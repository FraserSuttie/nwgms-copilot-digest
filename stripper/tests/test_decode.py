"""Decode CLI tests (ADR 0007): round trips for every decodable prefix, the --thread table, refusals, terminal-only
output, the audit line, and that the table never contains thread body text. Synthetic data only, throwaway key
stores with the test-only protector, temp directories only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import decode as D  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import pipeline as P  # noqa: E402
import runner as RN  # noqa: E402
from test_mailfront import raw  # noqa: E402

TP = K.TestOnlyProtector()
NOW = datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.state = self.td / "state"
        self.gen = C.Gen(20261102)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def store(self) -> K.KeyStore:
        return K.KeyStore(self.state, TP)

    def decode(self, *args, out=None) -> tuple[int, str]:
        out = out if out is not None else Tty()
        rc = D.run(["--state", str(self.state), *args], out=out, protector=TP)
        return rc, out.getvalue()

    def audit_lines(self) -> list[dict]:
        p = self.state / "audit.log"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        return [d for d in map(json.loads, lines) if d.get("event") == "decode"]


class RoundTrips(Base):
    def test_codes_contact_attachment(self):
        s = self.store()
        vals = {"REF": self.gen.referral(), "SMP": self.gen.sample(), "WS": self.gen.worksheet(),
                "QC": self.gen.qc(), "CASE": self.gen.case()}
        toks = {c: s.token(c, v, 2026) for c, v in vals.items()}
        n = s.contact(email="pat.example@example.invalid", name="Quill Tenterden")
        s.attachment("f" * 32, 1, "Synthetic_Form_v2.pdf")
        s.link_block("a" * 32, "f" * 32)
        s.save()
        args = [*toks.values(), f"CONTACT-{n:04d}", "ATT-1@" + "a" * 12]
        rc, text = self.decode(*args)
        self.assertEqual(rc, 0)
        for c, v in vals.items():
            self.assertIn(f"{toks[c]} | {D.KINDS[c]} | {K.normalise_value(v)}", text)
        self.assertIn("Quill Tenterden <pat.example@example.invalid>", text)
        self.assertIn("Synthetic_Form_v2.pdf", text)
        self.assertIn("resolved 7, refused 0", text)

    def test_full_hash_and_case_of_hash(self):
        s = self.store()
        s.attachment("f" * 32, 2, "Agenda.docx")
        s.link_block("b" * 32, "f" * 32)
        s.save()
        self.assertEqual(self.decode("ATT-2@" + "b" * 32)[0], 0)


class Refusals(Base):
    def setUp(self):
        super().setUp()
        s = self.store()
        s.attachment("f" * 32, 1, "Notes.txt")
        s.link_block("c" * 12 + "1" * 20, "f" * 32)
        s.link_block("c" * 12 + "2" * 20, "e" * 32)
        s.save()

    def test_token_refusals(self):
        cases = ["[PT-NHS]", "[NAME-3]", "[CLIN]", "[DATE]", "[LOC]", "[URL]", "[GMC-ID]", "THR-BCDFGHJK",
                 "REF-BCDFGHJK", "REF-AEIOU123", "CONTACT-0999", "CONTACT-12", "ATT-1", "ATT-1@abc",
                 "ATT-1@" + "c" * 12, "ATT-9@" + "c" * 12 + "1" * 20, "hello"]
        for tok in cases:
            rc, text = self.decode(tok)
            self.assertEqual(rc, 1, tok)
            self.assertIn(f"{tok} | refused |", text)
        self.assertIn("no map by design", self.decode("[PT-DOB]")[1])
        self.assertIn("ambiguous", self.decode("ATT-1@" + "c" * 12)[1])

    def test_quarantined_message_attachment_not_decodable(self):
        s = self.store()
        s.attachment("d" * 32, 1, "Held.pdf")     # stored by the transform, never linked: not released
        s.save()
        self.assertEqual(self.decode("ATT-1@" + "d" * 12)[0], 1)

    def test_usage_and_paths(self):
        self.assertEqual(self.decode()[0], 2)
        for bad in (G.REPO_ROOT / "x", self.td / "OneDrive - Trust" / "s"):
            self.assertEqual(D.run(["--state", str(bad), "REF-BCDFGHJK"], out=Tty(), protector=TP), 2)
        self.assertEqual(D.run(["--state", str(self.td / "empty"), "REF-BCDFGHJK"], out=Tty(), protector=TP), 2)
        self.assertFalse((self.td / "empty").exists())    # no key store is created by decode

    def test_redirected_stdout_refused_and_not_audited(self):
        rc, text = self.decode("REF-BCDFGHJK", out=io.StringIO())
        self.assertEqual(rc, 2)
        self.assertIn("not a terminal", text)
        self.assertEqual(self.audit_lines(), [])

    def test_thread_and_tokens_together_refused(self):
        self.assertEqual(self.decode("--thread", "x.md", "REF-BCDFGHJK")[0], 2)

    def test_bad_thread_files(self):
        bad = self.td / "THR-BCDFGHJK.md"
        bad.write_text("# not a stripper file\n", encoding="utf-8")
        for p in (bad, self.td / "notes.md", self.td / "THR-BCDFGHJQ.md", G.REPO_ROOT / "THR-BCDFGHJK.md"):
            self.assertEqual(self.decode("--thread", str(p))[0], 2, p.name)


class Scan(unittest.TestCase):
    def test_thread_tokens_and_no_map_counts(self):
        h = "9" * 32
        text = (f"<!-- block:{h} -->\nREF-BCDFGHJK and [PT-NHS], [PT-DOB], [NAME-1], [CLIN] ATT-2 (pdf) REF-BCDFGHJK\n"
                f"<!-- /block:{h} -->\nREF-ZZZZZZZZ outside any block\n")
        toks, nm = D.thread_tokens(text)
        self.assertEqual(toks, ["REF-BCDFGHJK", "ATT-2@" + "9" * 12])
        self.assertEqual(nm, {"[PT-*]": 2, "[NAME-n]": 1, "[CLIN]": 1})


class Audit(Base):
    def test_audit_line_tokens_counts_no_values(self):
        s = self.store()
        v = self.gen.referral()
        t = s.token("REF", v, 2026)
        s.save()
        self.decode(t, "[PT-NHS]")
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1)
        a = lines[0]
        self.assertEqual((a["mode"], a["tokens"], a["counts"], a["refused"]), ("tokens", [t, "[PT-NHS]"], {"REF": 1}, 1))
        self.assertEqual(a["version"], D.VERSION)
        log = (self.state / "audit.log").read_text(encoding="utf-8")
        self.assertNotIn(K.normalise_value(v), log)
        self.assertNotIn(v, log)


class Thread(Base):
    """End to end: synthetic raw drop -> runner -> thread file -> --thread table."""

    def make_thread(self) -> Path:
        drop, out = self.td / "drop", self.td / "out"
        drop.mkdir()
        ref, smp = self.gen.referral(), self.gen.sample()
        self.vals = (ref, smp)
        body = (f"Hello,\n\nThe referral {ref} is booked in and sample {smp} has arrived in the lab today.\n\n"
                "Could you confirm the worksheet plan before Friday afternoon please?\n\nThanks,\nAda")
        msgs = [raw(body={"content_type": "text", "content": body},
                    to=[{"name": "Quill Tenterden", "address": "quill.tenterden@example.invalid"}],
                    attachments=[{"name": "Synthetic_Request_Form.pdf", "content_type": "application/pdf",
                                  "size": 1200, "is_inline": False}]),
                raw(internet_message_id="<synthetic-2@example.invalid>", sent="2026-10-30T10:02:00Z",
                    body={"content_type": "text", "content": "Hi Ada,\n\nThanks, that is confirmed and the update is noted.\n\nCallum"},
                    attachments=[{"name": "Minutes_draft.docx", "content_type": "application/msword",
                                  "size": 900, "is_inline": False}])]
        for i, m in enumerate(msgs):
            (drop / f"m{i}.json").write_text(json.dumps(m), encoding="utf-8")
        rep = RN.run(drop, self.state, out, C.directory_markdown(), vocab=P.corpus_vocab(), protector=TP,
                     use_ner=False, year=2026, now=NOW)
        self.assertEqual(rep.released, 2, dict(rep.reasons))
        files = sorted((out / "Comms" / "_Messages").glob("THR-*.md"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_table(self):
        f = self.make_thread()
        text = f.read_text(encoding="utf-8")
        rc, table = self.decode("--thread", str(f))
        self.assertEqual(rc, 0, table)
        self.assertTrue(table.startswith("token | kind | value\n"))
        for v in self.vals:
            self.assertIn(K.normalise_value(v), table)
        self.assertIn("Synthetic_Request_Form.pdf", table)
        self.assertIn("Minutes_draft.docx", table)
        self.assertRegex(table, r"CONTACT-\d{4} \| contact \| .*<quill\.tenterden@example\.invalid>")
        for h in re.findall(r"<!-- block:([0-9a-f]{32}) -->", text):
            self.assertIn(f"@{h[:12]} | attachment |", table)
        self.assertEqual(self.audit_lines()[-1]["mode"], "thread")

    def test_table_never_contains_body_text(self):
        f = self.make_thread()
        text = f.read_text(encoding="utf-8")
        _, table = self.decode("--thread", str(f))
        out_lines = set(table.splitlines())
        for block in re.findall(r"<!-- block:[0-9a-f]{32} -->\n(.*?)<!-- /block:", text, re.S):
            subject_and_body = block.split("**Subject:**", 1)[1]
            for line in (ln.strip() for ln in subject_and_body.splitlines()):
                if not line:
                    continue
                self.assertNotIn(line, out_lines)
                if len(line) >= 12:
                    self.assertNotIn(line, table)
        for word in ("booked", "arrived", "worksheet plan", "confirmed"):
            self.assertNotIn(word, table)


if __name__ == "__main__":
    unittest.main()
