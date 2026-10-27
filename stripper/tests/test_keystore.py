"""Key store, protector and review-state protection tests. Synthetic values only, temp directories only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402

TP = K.TestOnlyProtector()


class KeyStoreTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name) / "ks"
        self.addCleanup(self._td.cleanup)

    def test_requires_a_protector(self):
        with self.assertRaises(ValueError):
            K.KeyStore(self.dir, None)

    def test_refuses_repo_and_onedrive(self):
        with self.assertRaises(ValueError):
            K.KeyStore(ROOT / "stripper" / "ks", TP)
        with self.assertRaises(ValueError):
            K.KeyStore(Path(self._td.name) / "OneDrive - Org" / "ks", TP)

    def test_default_protector_refuses_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("Windows")
        with self.assertRaises(OSError):
            K.default_protector()

    def test_tokens_deterministic_and_normalised(self):
        s = K.KeyStore(self.dir, TP)
        a = s.token("REF", "R26-AB12", 2026)
        self.assertEqual(a, s.token("REF", "r26 ab12", 2026))
        self.assertRegex(a, r"^REF-[BCDFGHJKLMNPQRSTVWXZ]{8}$")
        self.assertNotEqual(a, s.token("SMP", "R26-AB12", 2026))  # category is part of the input

    def test_year_key_changes_token_and_master_changes_token(self):
        s = K.KeyStore(self.dir, TP)
        self.assertNotEqual(s.year_key(2026), s.year_key(2027))
        self.assertNotEqual(s.token("REF", "R26AB12", 2026), s.token("REF", "R26AB12", 2027))
        other = K.KeyStore(Path(self._td.name) / "ks2", TP)
        self.assertNotEqual(s.token("REF", "R26AB12", 2026), other.token("REF", "R26AB12", 2026))

    def test_persist_and_resolve_round_trip(self):
        s = K.KeyStore(self.dir, TP)
        ref, smp = s.token("REF", "R26-AB12", 2026), s.token("SMP", "S26-CD34", 2026)
        n = s.contact("pat@example.org", "Pat Example")
        s.save()
        s2 = K.KeyStore(self.dir, TP)
        self.assertEqual(s2.token("REF", "R26-AB12", 2026), ref)
        self.assertEqual(s2.resolve(ref), "R26AB12")
        self.assertEqual(s2.resolve(smp), "S26CD34")
        self.assertIn("e:pat@example.org", s2.resolve(f"CONTACT-{n:04d}"))
        self.assertIsNone(s2.resolve("REF-BBBBBBBB"))
        self.assertIsNone(s2.resolve("garbage"))

    def test_files_are_protected_not_plain(self):
        s = K.KeyStore(self.dir, TP)
        s.token("REF", "R26AB12", 2026)
        s.save()
        raw = (self.dir / K.KeyStore.TABLES).read_bytes()
        self.assertTrue(raw.startswith(TP.MARK))  # went through the protector
        self.assertFalse((self.dir / "tables.json").exists())

    def test_collision_gets_longer_code(self):
        s = K.KeyStore(self.dir, TP)
        first = s.token("REF", "R26AAAA", 2026)
        # force the next value's first-choice code to be taken
        digest_code = first.split("-", 1)[1]
        import hashlib, hmac
        d = hmac.new(s.year_key(2026), b"REF|R26BBBB", hashlib.sha256).digest()
        s._tok[("REF", K._encode(d, K.START_LEN))] = "9999|X"
        second = s.token("REF", "R26BBBB", 2026)
        self.assertEqual(len(second.split("-", 1)[1]), K.START_LEN + 2)
        self.assertNotEqual(first, second)
        self.assertEqual(s.token("REF", "R26BBBB", 2026), second)  # stable afterwards
        self.assertTrue(digest_code)

    def test_contact_links_name_and_email(self):
        s = K.KeyStore(self.dir, TP)
        a = s.contact("x@example.org", "Pat Example")
        self.assertEqual(a, s.contact("X@example.org", ""))
        self.assertEqual(a, s.contact("", "Example, Pat"))
        self.assertNotEqual(a, s.contact("y@example.org", "Other Person"))
        with self.assertRaises(ValueError):
            s.contact("", "")

    def test_audit_line_has_no_content(self):
        s = K.KeyStore(self.dir, TP)
        s.audit(s.message_hash("some synthetic text"), "stripped", Counter({"REF": 2, "PT-NHS": 1}), "v-test")
        line = json.loads((self.dir / K.KeyStore.AUDIT).read_text().strip())
        self.assertEqual(set(line), {"ts", "msg", "decision", "counts", "version"})
        self.assertNotIn("synthetic", json.dumps(line))
        self.assertEqual(len(line["msg"]), 32)

    def test_message_hash_is_keyed(self):
        a = K.KeyStore(self.dir, TP)
        b = K.KeyStore(Path(self._td.name) / "ks2", TP)
        self.assertEqual(a.message_hash("t"), a.message_hash("t"))
        self.assertNotEqual(a.message_hash("t"), b.message_hash("t"))


class StateProtectionTests(unittest.TestCase):
    def test_protected_state_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = Path(d) / G.State.FILE
            legacy.write_text(json.dumps({"staff": ["dana moss"], "external": [], "ignore": [], "counts": {"zed quux": 3}}))
            st = G.State(Path(d), TP)
            self.assertFalse(legacy.exists())  # plaintext removed
            self.assertTrue((Path(d) / G.State.PFILE).read_bytes().startswith(TP.MARK))
            self.assertEqual(G.State(Path(d), TP).data["staff"], ["dana moss"])
            self.assertEqual(st.purge_counts(), 1)
            st.save()
            st2 = G.State(Path(d), TP)
            self.assertEqual(st2.data["counts"], {})
            self.assertEqual(st2.data["staff"], ["dana moss"])  # classifications survive a purge


@unittest.skipUnless(sys.platform == "win32", "DPAPI smoke test runs on Windows only")
class DpapiSmoke(unittest.TestCase):
    def test_round_trip(self):
        p = K.DpapiProtector()
        blob = p.protect(b"synthetic bytes")
        self.assertNotEqual(blob, b"synthetic bytes")
        self.assertEqual(p.unprotect(blob), b"synthetic bytes")
        with tempfile.TemporaryDirectory() as d:
            s = K.KeyStore(Path(d) / "ks", p)
            t = s.token("REF", "R26AB12", 2026)
            s.save()
            self.assertEqual(K.KeyStore(Path(d) / "ks", p).resolve(t), "R26AB12")


if __name__ == "__main__":
    unittest.main()
