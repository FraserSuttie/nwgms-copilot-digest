"""Tests for the name gate and review queue. Run from the repo root:

    python -m unittest discover -s stripper/tests -v

NER spans are supplied by hand, so these tests need no spaCy model.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("stripper", "stripper/corpus", "validator"):
    sys.path.insert(0, str(ROOT / sub))

import generate as C  # noqa: E402
import namegate as G  # noqa: E402
import recall as R  # noqa: E402
import review as RV  # noqa: E402
import keystore as K  # noqa: E402

TP = K.TestOnlyProtector()

DIRECTORY = C.directory_markdown()


def ppl(header=()):
    return G.parse_directory(DIRECTORY).with_header(header)


def run(text, names, people=None, **kw):
    """Gate `text` where `names` are the substrings the NER layer found. Returns {substring: (action, reason)}."""
    spans = []
    for n in names:
        i = text.index(n)
        spans.append((i, i + len(n)))
    dec, _ = G.gate(text, spans, people or ppl(), **kw)
    return {text[d.start:d.end]: (d.action, d.reason) for d in dec}


class DirectoryTests(unittest.TestCase):
    def test_parse_reads_full_name_column_only(self):
        p = G.parse_directory(DIRECTORY)
        self.assertIn("ada fairweather", p.full)
        self.assertNotIn("synthetic role", p.full)
        self.assertEqual(len(p.tokens["ridgeway"]), 2)

    def test_surname_comma_first_and_titles_normalise(self):
        self.assertEqual(G.norm("Fairweather, Ada"), "ada fairweather")
        self.assertEqual(G.norm("Dr. Ada Fairweather"), "ada fairweather")


class GateRules(unittest.TestCase):
    def test_full_directory_name_passes(self):
        self.assertEqual(run("Please ask Ada Fairweather to look.", ["Ada Fairweather"])["Ada Fairweather"],
                         ("pass", "FULL_DIR"))

    def test_unknown_full_name_is_stripped_even_if_first_name_is_staff(self):
        r = run("The patient is Ada Quillfeather today.", ["Ada Quillfeather"])
        self.assertEqual(r["Ada Quillfeather"], ("name", "UNKNOWN_FULL"))

    def test_header_participant_passes_with_trust_domain_only(self):
        hdr = [("Hester Brindle", "hester.brindle@mft.nhs.uk"), ("Zed Outsider", "z@example.com")]
        r = run("Ask Hester Brindle and Zed Outsider.", ["Hester Brindle", "Zed Outsider"], ppl(hdr))
        self.assertEqual(r["Hester Brindle"], ("pass", "FULL_HEADER"))
        self.assertEqual(r["Zed Outsider"][0], "name")

    def test_bare_name_in_greeting_signoff_and_speaker_positions(self):
        self.assertEqual(run("Hi Callum,\n\nok", ["Callum"])["Callum"], ("pass", "BARE_GREETING"))
        self.assertEqual(run("Thanks,\nCallum\n", ["Callum"])["Callum"], ("pass", "BARE_SIGNOFF"))
        self.assertEqual(run("Tallis: hello", ["Tallis"])["Tallis"], ("pass", "BARE_SPEAKER"))

    def test_bare_name_mid_sentence_is_stripped(self):
        self.assertEqual(run("mum says Callum is unwell", ["Callum"])["Callum"], ("name", "BARE_MIDSENTENCE"))

    def test_bare_name_mid_sentence_passes_when_full_name_also_present(self):
        t = "Ada Fairweather will chase it. Ada to confirm."
        r = G.gate(t, [(0, 15), (31, 34)], ppl())[0]
        self.assertEqual([(d.action, d.reason) for d in r], [("pass", "FULL_DIR"), ("pass", "BARE_WITH_FULL")])

    def test_ambiguous_surname_is_stripped_even_in_greeting(self):
        self.assertEqual(run("Hi Ridgeway,", ["Ridgeway"])["Ridgeway"], ("name", "AMBIGUOUS"))

    def test_unknown_bare_name_stripped(self):
        self.assertEqual(run("Hi Zorbulon,", ["Zorbulon"])["Zorbulon"], ("name", "UNKNOWN_BARE"))

    def test_ignore_list_passes_non_person(self):
        r = run("Open Epic now", ["Epic"], ignore=frozenset({"epic"}))
        self.assertEqual(r["Epic"], ("pass", "IGNORED"))

    def test_missed_directory_names_are_found_without_ner(self):
        dec, _ = G.gate("Tallis: and Priya Lindqvist: x", [], ppl())
        self.assertEqual([(d.action, d.reason) for d in dec], [("pass", "BARE_SPEAKER"), ("pass", "FULL_DIR")])

    def test_span_crossing_a_line_break_is_split(self):
        t = "G.\nAda Fairweather\n0161"
        dec, _ = G.gate(t, [(0, len("G.\nAda Fairweather"))], ppl())
        self.assertIn(("pass", "FULL_DIR"), [(d.action, d.reason) for d in dec])

    def test_numbering_is_stable_per_message_and_reuses_tokens(self):
        t = "Ivo Kestrel met Dana Moss. Moss said Kestrel agreed."
        spans = [(0, 11), (16, 25), (27, 31), (37, 44)]
        dec, _ = G.gate(t, spans, ppl())
        nums = {t[d.start:d.end]: d.n for d in dec}
        self.assertEqual(nums, {"Ivo Kestrel": 1, "Dana Moss": 2, "Moss": 2, "Kestrel": 1})

    def test_decisions_carry_no_text(self):
        self.assertEqual(set(G.Decision.__dataclass_fields__), {"start", "end", "action", "reason", "n"})


class ReviewAndState(unittest.TestCase):
    def test_unknown_names_counted_once_per_message(self):
        rev = Counter()
        run("Dana Moss and Dana Moss again", ["Dana Moss"], review=rev)
        G.gate("Dana Moss x. Dana Moss y.", [(0, 9), (13, 22)], ppl(), review=rev)
        self.assertEqual(rev["dana moss"], 2)  # one per message, not per mention

    def test_state_dir_guard(self):
        with self.assertRaises(ValueError):
            G.check_state_dir(ROOT / "stripper" / "state")
        with self.assertRaises(ValueError):
            G.check_state_dir(Path(tempfile.gettempdir()) / "OneDrive - Example" / "z3")

    def test_classify_staff_extends_people_and_prints_directory_row(self):
        with tempfile.TemporaryDirectory() as d:
            st = G.State(Path(d), TP)
            st.merge_counts(Counter({"dana moss": 3}))
            st.save()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(RV.main(["--state", d, "classify", "Dana Moss", "staff"], TP), 0)
            self.assertIn("| Moss | Dana Moss | TBC | NWGMS | |", out.getvalue())
            st2 = G.State(Path(d), TP)
            self.assertNotIn("dana moss", st2.data["counts"])
            self.assertEqual(run("Ask Dana Moss.", ["Dana Moss"], st2.people(G.parse_directory(DIRECTORY)))["Dana Moss"],
                             ("pass", "FULL_LOCAL"))

    def test_list_shows_names_and_counts_only(self):
        with tempfile.TemporaryDirectory() as d:
            st = G.State(Path(d), TP)
            st.merge_counts(Counter({"dana moss": 3, "once only": 1}))
            st.save()
            out = io.StringIO()
            with redirect_stdout(out):
                RV.main(["--state", d, "list", "--min", "2"], TP)
            self.assertIn("dana moss", out.getvalue())
            self.assertNotIn("once only", out.getvalue())

    def test_cli_refuses_state_dir_in_repo(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(RV.main(["--state", str(ROOT / "stripper" / "state"), "list"], TP), 2)


class GateOnCorpus(unittest.TestCase):
    msgs = C.generate(400, seed=21)

    def test_gate_never_passes_a_patient_external_or_non_staff_name(self):
        _, _, _, _, _, extra = R.evaluate(self.msgs, use_ner=False, use_gate=True)
        self.assertEqual(extra["gate_leaks"], 0)

    def test_directory_staff_are_not_false_stripped(self):
        per, *_ = R.evaluate(self.msgs, use_ner=False, use_gate=True)
        for cat in ("staff_name_directory", "staff_name_header"):
            self.assertGreater(per[cat]["planted"], 0)
            self.assertEqual(per[cat]["touched"], 0, cat)

    def test_ambiguous_and_collision_cases_are_stripped_without_ner(self):
        per, adv, *_ = R.evaluate(self.msgs, use_ner=False, use_gate=True)
        self.assertEqual(per["staff_name_ambiguous"]["full"], per["staff_name_ambiguous"]["planted"])
        self.assertEqual(adv["name_collision"]["full"], adv["name_collision"]["planted"])


if __name__ == "__main__":
    unittest.main()
