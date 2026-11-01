"""Email front end tests: contract, normaliser edge cases, message record (ADR 0006). Synthetic data only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import copy
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
import mailfront as M  # noqa: E402


def raw(**over) -> dict:
    r = {"schema": "nwgms-rawdrop/1", "internet_message_id": "<synthetic-1@example.invalid>",
         "conversation_id": "synthetic-conversation-1", "sent": "2026-10-30T09:14:00Z", "direction": "received",
         "from": {"name": "Ada Fairweather", "address": "ada.fairweather@mft.nhs.uk"},
         "to": [{"name": "Callum Ridgeway", "address": "callum.ridgeway@nhs.net"}],
         "subject": "Panel question", "body": {"content_type": "text", "content": "Hi Callum,\n\nAll fine.\n\nThanks,\nAda"},
         "attachments": []}
    r.update(over)
    return r


def norm(content: str, ctype: str = "html") -> tuple[str, Counter, int]:
    f = Counter()
    text, img = M.normalise_body({"content_type": ctype, "content": content}, f)
    return text, f, img


class Contract(unittest.TestCase):
    def test_valid(self):
        self.assertIs(M.validate(raw())["schema"], "nwgms-rawdrop/1")

    def test_rejects(self):
        bad = [raw(extra="x"), {k: v for k, v in raw().items() if k != "subject"}, raw(schema="nwgms-rawdrop/2"),
               raw(direction="inbound"), raw(sent="yesterday"), raw(conversation_id=" "),
               raw(to=[{"name": "x"}]), raw(body={"content_type": "rtf", "content": ""}),
               raw(attachments=[{"name": "a.pdf"}]), raw(**{"from": {"name": "x", "address": "no-at"}}), []]
        for b in bad:
            with self.assertRaises(M.ContractError):
                M.validate(b)

    def test_error_text_has_no_values(self):
        try:
            M.validate(raw(to=[{"name": "Planted Value", "address": "x"}]))
        except M.ContractError as exc:
            self.assertNotIn("Planted", str(exc))


class Html(unittest.TestCase):
    def test_table_rows_and_cells(self):
        t, _, _ = norm("<table><tr><th>Test</th><th>status</th></tr><tr><td>panel</td><td>done</td></tr></table>")
        self.assertEqual(t, "Test | status\npanel | done")

    def test_style_script_head_dropped_entities_unescaped(self):
        t, _, _ = norm("<html><head><title>x</title><style>p{}</style></head><body><script>bad()</script>"
                       "<p>a&nbsp;b &amp; c</p></body></html>")
        self.assertEqual(t, "a b & c")

    def test_inline_images_counted_never_kept(self):
        t, _, img = norm('<p>see</p><img src="cid:image001.png"><img src="data:image/png;base64,AAAA">')
        self.assertEqual((t, img), ("see", 2))

    def test_outlook_reply_div_cuts_history(self):
        t, f, _ = norm('<p>new</p><div id="divRplyFwdMsg"><b>From:</b> x</div><div><p>old text</p></div>')
        self.assertEqual(t, "new")
        self.assertTrue(f["quoted_history"])

    def test_blockquote_cuts(self):
        t, _, _ = norm("<p>new</p><p>On Wed someone wrote:</p><blockquote><p>old</p></blockquote>")
        self.assertNotIn("old", t)

    def test_signature_div_cuts(self):
        t, f, _ = norm('<p>new</p><div id="Signature"><p>Name<br>Role<br>Synthetic Desk</p></div>')
        self.assertEqual(t, "new")
        self.assertTrue(f["signature"])


class TextCuts(unittest.TestCase):
    def test_markers(self):
        for marker in ["-----Original Message-----\nFrom: x\nSent: y", "________________________________\nFrom: x",
                       "On Wed, 1 Oct 2026, X wrote:\n> old", "> old", "From: x\nTo: y\nSent: z\n\nold"]:
            t, _, _ = norm("new\n\n" + marker + "\nold", "text")
            self.assertEqual(t, "new", marker)

    def test_forward_flag(self):
        t, f, _ = norm("fyi\n\n---------- Forwarded message ---------\nFrom: x\n\nold", "text")
        self.assertEqual((t, f["forwarded_dropped"]), ("fyi", 1))

    def test_from_without_sent_is_not_history(self):
        t, _, _ = norm("From: the lab team, all fine.\nnext line", "text")
        self.assertIn("next line", t)

    def test_disclaimer_and_banner(self):
        t, f, _ = norm("CAUTION: This email originated from outside the organisation.\n\nbody\n\n"
                       "This message may contain confidential information. Switchboard Synthetic Desk.", "text")
        self.assertEqual((t, f["disclaimer"]), ("body", 2))

    def test_signoff_keeps_name_drops_rest(self):
        t, f, _ = norm("body\n\nKind regards,\nAda Fairweather\nSynthetic Role\nSynthetic Desk", "text")
        self.assertEqual(t, "body\n\nKind regards,\nAda Fairweather")
        self.assertTrue(f["signature"])

    def test_signoff_only_in_tail(self):
        body = "Thanks\n" + "\n".join(f"line {i}" for i in range(20))
        t, _, _ = norm(body, "text")
        self.assertIn("line 19", t)

    def test_dash_dash_and_sent_from(self):
        self.assertEqual(norm("body\n-- \nsig", "text")[0], "body")
        self.assertEqual(norm("body\nSent from my phone", "text")[0], "body")


class Encodings(unittest.TestCase):
    def test_mojibake_repaired(self):
        bad = "Here’s it".encode("utf-8").decode("cp1252")
        t, f, _ = norm(bad, "text")
        self.assertEqual((t, f["mojibake_repaired"]), ("Here’s it", 1))

    def test_zero_width_nbsp_crlf_bom(self):
        t, _, _ = norm("﻿a b\r\nTha​nks­ x", "text")
        self.assertEqual(t, "a b\nThanks x")

    def test_unrepairable_mojibake_left_and_flagged(self):
        t, f, _ = norm("Ã€� and 中", "text")
        self.assertTrue(f["mojibake_left"] or f["mojibake_repaired"] == 0)


class Record(unittest.TestCase):
    def test_fields_and_header(self):
        r = M.build(raw(cc=[{"name": "Wetherbourne, Rosalind (Supplier)", "address": "r.w@example.org"}],
                        subject="RE: FW:  Panel   question",
                        attachments=[{"name": "CRF-2026-019 v2.pdf", "content_type": "application/pdf", "size": 10, "is_inline": False},
                                     {"name": "image001.png", "content_type": "image/png", "size": 5, "is_inline": True}]))
        self.assertEqual([r.fields[k] for k in M.HEADER_FIELDS], ["Ada Fairweather", "Callum Ridgeway", "r.w@example.org"])
        self.assertEqual(r.fields["message"].split("\n\n")[0], "Panel question")
        self.assertEqual(r.fields["attachment_1"], "CRF-2026-019 v2.pdf")
        self.assertNotIn("attachment_2", r.fields)
        self.assertEqual((r.inline_images, r.to_n, r.cc_n), (1, 1, 1))
        self.assertIn(("Rosalind Wetherbourne", "r.w@example.org"), r.header_people)
        self.assertTrue(r.flags["forwarded_subject"])

    def test_unique_body_preferred(self):
        r = M.build(raw(unique_body={"content_type": "text", "content": "only new"}))
        self.assertTrue(r.fields["message"].endswith("only new"))

    def test_reply_to(self):
        self.assertEqual(M.build(raw()).reply_to, "none")
        same = [{"name": "x", "address": "ADA.fairweather@mft.nhs.uk"}]
        self.assertEqual(M.build(raw(reply_to=same)).reply_to, "same as sender")
        self.assertEqual(M.build(raw(reply_to=[{"name": "x", "address": "y@example.org"}])).reply_to, "differs (1)")

    def test_sent_utc_minutes(self):
        self.assertEqual(M.build(raw(sent="2026-10-30T10:14:59+01:00")).sent, "2026-10-30 09:14Z")

    def test_display_name(self):
        self.assertEqual(M.display_name('"Ridgeway, Callum (MFT)"'), "Callum Ridgeway")
        self.assertEqual(M.display_name("Ada Fairweather [NWGMS]"), "Ada Fairweather")

    def test_split_check(self):
        good = {"header_from": "a", "header_to": "b", "header_cc": "none", "message": "s\n\nb"}
        self.assertFalse(M.split_check(good))
        for bad in ({**good, "message": "no split"}, {**good, "header_to": "b\nc"}, {**good, "header_cc": " "},
                    {k: v for k, v in good.items() if k != "header_from"}):
            self.assertTrue(M.split_check(bad))

    def test_build_does_not_mutate(self):
        r0 = raw()
        r1 = copy.deepcopy(r0)
        M.build(r0)
        self.assertEqual(r0, r1)



class NerSpans(unittest.TestCase):
    def test_span_across_line_break_is_split_not_dropped(self):
        import ner as N
        t = "To: Ewan Tallis\nCc: none\n\nZebedee\n Quolworth"
        a = t.index("Ewan")
        self.assertEqual([t[s:e] for s, e in N.split_lines(t, a, t.index(":", a))], ["Ewan Tallis", "Cc"])
        z = t.index("Zebedee")
        self.assertEqual([t[s:e] for s, e in N.split_lines(t, z, len(t))], ["Zebedee", "Quolworth"])
        self.assertEqual(N.split_lines(t, 0, 2), [(0, 2)])


if __name__ == "__main__":
    unittest.main()
