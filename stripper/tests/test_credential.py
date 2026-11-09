"""Credential detector tests (ADR 0012). Invented values only; no shape is copied from a real message.

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

import detectors as D  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import transform as T  # noqa: E402


def creds(text: str):
    t, hits = D.detect(text)
    return [t[h.start:h.end] for h in hits if h.code == "CREDENTIAL"]


class DetectTests(unittest.TestCase):
    def test_curly_quoted_temporary_password(self):
        self.assertEqual(creds("I’ve unlocked it and given you the temporary password “zorbquim41”.\n"), ["zorbquim41"])

    def test_straight_quoted_password(self):
        self.assertEqual(creds('new password "Plinth-77x" for now'), ["Plinth-77x"])

    def test_single_quoted_after_space(self):
        self.assertEqual(creds("password 'qwerble9' set"), ["qwerble9"])

    def test_username_label_on_own_line(self):
        self.assertEqual(creds("Please check.\n\nUsername: qwib.zarnell\n"), ["qwib.zarnell"])

    def test_password_is_and_equals_and_digits(self):
        self.assertEqual(creds("the password is Glarn2026."), ["Glarn2026"])
        self.assertEqual(creds("pwd = 48213977"), ["48213977"])  # claimed before the 8-digit MRN rule

    def test_pin_and_login_id(self):
        self.assertEqual(creds("PIN: 4471"), ["4471"])
        self.assertEqual(creds("login id - zz.quorm"), ["zz.quorm"])

    def test_no_value_no_hit(self):
        for s in ("your password has expired", "please reset your password.", "Username and password needed",
                  "the user’s password was reset", "pin the note to the board"):
            self.assertEqual(creds(s), [], s)

    def test_apostrophe_is_not_an_opening_quote(self):
        self.assertEqual(creds("the password's been reset, it's fine"), [])


class TransformTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        store = K.KeyStore(Path(td.name) / "ks", K.TestOnlyProtector())
        self.tr = T.Transformer(store, G.parse_directory(C.directory_markdown()), year=2026)

    def test_value_becomes_fixed_placeholder(self):
        out = self.tr.text("given you the temporary password “zorbquim41”.\nUsername: qwib.zarnell").text
        self.assertNotIn("zorbquim41", out)
        self.assertNotIn("qwib.zarnell", out)
        self.assertEqual(out.count("[CRED]"), 2)

    def test_placeholder_is_approved(self):
        self.assertTrue(T.APPROVED_RE.fullmatch("[CRED]"))

    def test_residual_scan_does_not_flag_its_own_placeholder(self):
        # 0.8.5: the residual scan blanks [CRED] to a space; a quoted space is not a credential.
        for s in ("given you the temporary password \u201czorbquim41\u201d.", "password 'qwerble9' set",
                  'new password "Plinth-77x" for now', "Username: qwib.zarnell\nThanks"):
            out = self.tr.text(s).text
            _, hits = D.detect(T.blank_approved(out))
            self.assertEqual([h.code for h in hits if h.code == "CREDENTIAL"], [], s)

    def test_whitespace_only_quote_is_not_a_credential(self):
        self.assertEqual(creds('the password " " was blank'), [])


if __name__ == "__main__":
    unittest.main()
