"""Caps diagnostic, vocab tool and limit tests (ADR 0010). Invented words only, temp directories only.

    python -m unittest discover -s stripper/tests -v
"""

from __future__ import annotations

import builtins
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stripper"))
sys.path.insert(0, str(ROOT / "stripper" / "corpus"))
import capsdiag as D  # noqa: E402
import capsscan as S  # noqa: E402
import capsvocab as V  # noqa: E402
import generate as C  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import runner as RN  # noqa: E402
import transform as T  # noqa: E402
from test_mailfront import raw  # noqa: E402

TP = K.TestOnlyProtector()
WORDS = ("Zorblax", "Quendle", "Vrimble", "Wibblefrump", "Snorfel")   # invented; none is a name or a vocabulary word
NONE = frozenset()


def body(text: str) -> dict:
    return {"content_type": "text", "content": text}


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.drop, self.state = self.td / "drop", self.td / "state"
        self.drop.mkdir()
        K.KeyStore(self.state, TP)                      # an existing baseline state: master key only
        self.directory = self.td / "directory.md"
        self.directory.write_text(C.directory_markdown(), encoding="utf-8")
        self.vocab = self.td / "vocab.txt"
        self.n = 0

    def put(self, conv: str, text: str, subject: str = "plain subject") -> None:
        self.n += 1
        m = raw(internet_message_id=f"<m{self.n}@x.invalid>", conversation_id=conv, subject=subject,
                sent=f"2026-10-30T09:{self.n:02d}:00Z", body=body(text))
        (self.drop / f"m{self.n}.json").write_text(json.dumps(m), encoding="utf-8")

    def fixture(self) -> None:
        """Hand-counted: 3 threads, 1 duplicate file, one released message.
        c1 msg1: subject Title Case x3 (Weekly Zorblax Review), body Quendle, Vrimble-Hub, Zorblax, Status, Owner
        c1 msg2: subject x3, body Zorblax's
        c2: Zorblax again; c3: Quendle; c4: a patient-cue word; c5: nothing."""
        self.put("c1", "Hi Callum,\n\nThe Quendle report is late. Please check the Vrimble-Hub export.\n\nZorblax\nStatus\nOwner\n\nThanks,\nAda",
                 "Weekly Zorblax Review")
        self.put("c1", "Hi Ada,\n\nAll fine with Zorblax's feed.\n\nThanks,\nCallum", "Re: Weekly Zorblax Review")
        self.put("c2", "Hi Callum,\n\nThe Zorblax feed is late again.\n\nThanks,\nAda")
        self.put("c3", "Hi Callum,\n\nThe Quendle feed is late.\n\nThanks,\nAda")
        self.put("c4", "Hi Callum,\n\nWe saw the patient Wibblefrump today.\n\nThanks,\nAda")
        self.put("c5", "Hi Callum,\n\nAll fine, nothing to report.\n\nThanks,\nAda")
        shutil.copy(self.drop / "m6.json", self.drop / "dup.json")

    def scan(self, **kw) -> S.ScanResult:
        return S.scan_drop(self.drop, self.state, self.directory.read_text(encoding="utf-8"), kw.pop("vocab", NONE),
                           use_ner=False, protector=TP, year=2026, **kw)

    def tree(self) -> dict:
        return {str(p.relative_to(self.td)): p.read_bytes() for p in sorted(self.td.rglob("*")) if p.is_file()}

    def assert_no_words(self, text: str) -> None:
        for w in WORDS:
            self.assertNotIn(w.lower(), text.lower())


@contextmanager
def write_guard(allowed: Path | None = None):
    """Any write, create, rename, delete or mkdir fails, except opening `allowed` for append."""
    real_open = builtins.open
    allowed_s = str(allowed.resolve()) if allowed else None

    def guarded(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            if not (allowed_s and str(Path(file).resolve()) == allowed_s and "a" in mode and "+" not in mode):
                raise AssertionError("write attempted to a path the tool must not write")
        return real_open(file, mode, *a, **k)

    def deny(*a, **k):
        raise AssertionError("filesystem change attempted")

    with mock.patch.object(builtins, "open", guarded), mock.patch.object(io, "open", guarded), \
            mock.patch.object(os, "replace", deny), mock.patch.object(os, "rename", deny), \
            mock.patch.object(os, "remove", deny), mock.patch.object(os, "unlink", deny), \
            mock.patch.object(os, "mkdir", deny), mock.patch.object(os, "makedirs", deny):
        yield


class Scanner(unittest.TestCase):
    """unknown_cap_hits is the one definition of the count; caps_profile only describes it."""
    TEXTS = ["The Zorblax report is late.", "Zorblax is down", "Zorblax\nQuendle\n", "Weekly Zorblax Review\n\nBody Quendle's here",
             "See Vrimble-Hub and Snorfel. Then Wibblefrump?", "NHS CRF iGene is fine", "- Zorblax item\n- Please check",
             "[NAME-1] and Zorblax", "Hi Callum,\n\nThanks, Ada"]

    def test_hits_match_words_and_count(self):
        for t in self.TEXTS:
            _, hits = R.unknown_cap_hits(t, NONE)
            self.assertEqual([h.word for h in hits], R.unknown_cap_words(t, NONE))
            self.assertEqual(len(hits), R.unknown_caps(t, NONE))
            self.assertEqual(R.caps_profile(t, NONE)["total"], len(hits))

    def test_known_counts(self):
        f = lambda t: R.unknown_cap_words(t, NONE)  # noqa: E731
        self.assertEqual(f("The Zorblax report is late."), ["Zorblax"])
        self.assertEqual(f("Zorblax is down"), [])                       # ordinary sentence start
        self.assertEqual(f("Zorblax\nQuendle\n"), ["Zorblax", "Quendle"])  # bare words alone on a line
        self.assertEqual(f("NHS CRF iGene ab Of"), [])
        self.assertEqual(f("See Vrimble-Hub."), ["Vrimble-Hub"])
        self.assertEqual(f("[NAME-1] said"), [])
        self.assertEqual(f("The Zorblax report", ), ["Zorblax"])
        self.assertEqual(R.unknown_cap_words("The Zorblax feed", frozenset({"zorblax"})), [])
        self.assertEqual(R.unknown_cap_words("The Vrimble-Hub feed", frozenset({"vrimble"})), [])

    def test_profile_shapes(self):
        p = R.caps_profile("Weekly Zorblax Review", NONE)
        self.assertEqual((p["total"], p["run3plus"], p["titlecase_line"], p["initial"], p["mid"]), (3, 3, 3, 1, 2))
        p = R.caps_profile("We use the Vrimble-Hub and Zorblax's feed.", NONE)
        self.assertEqual((p["total"], p["hyphen"], p["possessive"], p["isolated"], p["run2"]), (2, 1, 1, 2, 0))
        p = R.caps_profile("We use Quendle Snorfel daily", NONE)
        self.assertEqual((p["total"], p["run2"], p["isolated"], p["titlecase_line"]), (2, 2, 0, 0))
        p = R.caps_profile("Intro\n\nZorblax\n\nmore", NONE)
        self.assertEqual((p["alone_on_line"], p["initial"]), (2, 2))      # "Intro" and "Zorblax" are both bare lines

    def test_parts_sum_to_whole(self):
        t = "Weekly Zorblax Review\n\nThe Quendle feed and Vrimble."
        whole = R.caps_profile(t, NONE)["total"]
        self.assertEqual(R.caps_profile(t, NONE, "subject")["total"] + R.caps_profile(t, NONE, "body")["total"], whole)
        self.assertEqual((R.caps_profile(t, NONE, "subject")["total"], R.caps_profile(t, NONE, "body")["total"]), (3, 2))

    def test_density_default_unchanged(self):
        self.assertEqual(R.density("The Zorblax feed", NONE)["DENS_CAPS"], 1)
        self.assertEqual(R.density("The Zorblax feed", NONE, caps=False)["DENS_CAPS"], 0)
        self.assertEqual(R.Limits().unknown_caps, 1)


class PipelineEvaluate(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.store = K.KeyStore(self.td / "ks", TP)
        self.q = Q.Quarantine(self.td / "ks", TP)
        self.tr = T.Transformer(self.store, G.parse_directory(C.directory_markdown()), year=2026)

    def pipe(self, n=1):
        return P.Pipeline(self.tr, self.q, limits=R.Limits(unknown_caps=n))

    def tree(self):
        return {str(p): p.read_bytes() for p in sorted(self.td.rglob("*")) if p.is_file()}

    def test_evaluate_matches_process_and_writes_nothing(self):
        cases = [{"message": "Hi Callum,\n\nAll fine.\n\nThanks,\nAda"}, {"message": "The Zorblax feed is late."},
                 {"message": "x" * 25000}, {"message": "Call 01234567890 about the Quendle feed"}]
        for f in cases:
            before = self.tree()
            ev = self.pipe().evaluate(f)
            self.assertEqual(self.tree(), before)             # pure: no quarantine, audit or save
            o = self.pipe().process(f)
            self.assertEqual(o.status == "ok", not ev.reasons and ev.stripped is not None)
            self.assertEqual(o.reasons if o.status != "ok" else Counter(), ev.reasons if o.status != "ok" else Counter())
            if o.status == "ok":
                self.assertEqual(o.fields, ev.stripped)

    def test_limit_one_is_per_field(self):
        r = self.pipe(1).evaluate({"a": "The Zorblax feed", "b": "The Quendle feed"}).reasons
        self.assertEqual(r["DENS_CAPS"], 2)                   # byte-identical to 0.7.0: one hit per field

    def test_limit_above_one_is_message_total(self):
        f = {"a": "The Zorblax feed", "b": "The Quendle feed"}
        self.assertEqual(self.pipe(2).evaluate(f).reasons["DENS_CAPS"], 1)   # 1 + 1 = 2 reaches the limit
        self.assertEqual(self.pipe(3).evaluate(f).reasons["DENS_CAPS"], 0)
        self.assertEqual(self.pipe(2).evaluate({"a": "The Zorblax feed", "b": "fine"}).reasons["DENS_CAPS"], 0)


class RunnerLimit(Base):
    def test_report_prints_limit_and_flag_validated(self):
        self.fixture()
        out = self.td / "out"
        rep = RN.run(self.drop, self.td / "st2", out, C.directory_markdown(), protector=TP, use_ner=False, year=2026,
                     limits=R.Limits(unknown_caps=3))
        self.assertEqual(rep.caps_limit, 3)
        self.assertEqual(RN.report_counts(rep)["unknown-caps limit"], 3)
        self.assertEqual(RN.report_counts(RN.Report())["unknown-caps limit"], 1)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(RN.main(["--drop", str(self.drop), "--state", str(self.td / "s3"), "--out", str(self.td / "o3"),
                                      "--directory", str(self.directory), "--caps-limit", "0"]), 2)


class Diagnostic(Base):
    def report(self) -> str:
        self.fixture()
        return "\n".join(D.build_report(self.scan()))

    def value(self, rep: str, label: str) -> str:
        m = re.search(rf"^{re.escape(label)}: (.*)$", rep, re.M)
        self.assertIsNotNone(m, label)
        return m.group(1)

    def test_hand_counted(self):
        rep = self.report()
        self.assertEqual(self.value(rep, "files read"), "7")
        self.assertEqual(self.value(rep, "duplicates skipped"), "1")
        self.assertEqual(self.value(rep, "messages evaluated"), "6")
        self.assertEqual(self.value(rep, "released today"), "1")
        # c4's patient-cue sentence is also CUE_NAME, so it is "plus other reasons"
        self.assertEqual(self.value(rep, "DENS_CAPS only"), "4")
        self.assertEqual(self.value(rep, "DENS_CAPS plus other reasons"), "1")
        self.assertEqual(self.value(rep, "distinct unknown words"), "8")   # Weekly Zorblax Review Quendle Vrimble-Hub Status Owner Wibblefrump
        self.assertEqual(self.value(rep, "distinct threads"), "5")
        self.assertEqual(self.value(rep, "after a patient cue"), "1, 1")

    def test_output_has_no_words(self):
        rep = self.report()
        self.assert_no_words(rep)
        self.assertIsNone(re.search(r"[^A-Za-z0-9_ ,.:;()=+/\-\n]", rep))

    def test_main_prints_counts_only_and_error_paths_print_no_words(self):
        self.fixture()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            rc = D.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory)],
                        protector=TP, use_ner=False, out=out)
        self.assertEqual(rc, 0)
        self.assert_no_words(out.getvalue() + err.getvalue())
        # a front-end error path: unreadable and invalid files are counted, never echoed
        (self.drop / "bad.json").write_text("{" + WORDS[0], encoding="utf-8")
        (self.drop / "bad2.json").write_text(json.dumps({"schema": WORDS[1]}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            D.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory)],
                   protector=TP, use_ner=False, out=out)
        self.assert_no_words(out.getvalue() + err.getvalue())
        self.assertIn("unreadable: 1", out.getvalue())
        self.assertIn("schema rejects: 1", out.getvalue())

    def test_read_only(self):
        self.fixture()
        before = self.tree()
        with write_guard():
            D.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory)],
                   protector=TP, use_ner=False, out=io.StringIO())
        self.assertEqual(self.tree(), before)

    def test_refusals(self):
        self.fixture()
        repo = G.REPO_ROOT
        for kw in ({"drop": repo / "x"}, {"state": repo / "x"}, {"state": self.td / "OneDrive - T" / "s"},
                   {"state": self.td / "nokeystore"}):
            a = {"drop": self.drop, "state": self.state, **kw}
            with redirect_stderr(io.StringIO()):
                rc = D.main(["--drop", str(a["drop"]), "--state", str(a["state"]), "--directory", str(self.directory)],
                            protector=TP, use_ner=False, out=io.StringIO())
            self.assertEqual(rc, 2, kw)
        self.assertFalse((self.td / "nokeystore").exists())      # refuses rather than create a key store

    def test_vocab_changes_groups(self):
        self.fixture()
        res = self.scan(vocab=frozenset({"Zorblax", "Quendle", "Vrimble", "Weekly", "Review", "Status", "Owner"}))
        self.assertEqual(sum(1 for m in res.msgs if not m.reasons), 5)


class VocabTool(Base):
    def go(self, answers: str, *extra, vocab=True, list_=False):
        self.fixture()
        out, inp, err = TTY(), TTY(answers), io.StringIO()
        argv = ["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory), *extra]
        if vocab:
            argv += ["--vocab", str(self.vocab)]
        if list_:
            argv.append("--list")
        with redirect_stderr(err):
            rc = V.main(argv, protector=TP, use_ner=False, out=out, inp=inp)
        return rc, out.getvalue(), err.getvalue()

    def test_list_prints_words_to_terminal_and_writes_nothing(self):
        self.fixture()
        before = self.tree()
        out, err = TTY(), io.StringIO()
        with write_guard(), redirect_stderr(err):
            rc = V.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory), "--list"],
                        protector=TP, use_ner=False, out=out, inp=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(self.tree(), before)
        self.assertIn("Zorblax", out.getvalue())
        self.assertIn("Quendle", out.getvalue())             # two threads: shown
        self.assertNotIn("Vrimble-Hub", out.getvalue())      # one thread only: hidden by default (min 2 threads)
        self.assert_no_words(err.getvalue())

    def test_all_shows_single_thread_words_and_flagged_last(self):
        rc, out, err = self.go("", "--all", list_=True, vocab=False)
        self.assertEqual(rc, 0)
        self.assertIn("Vrimble-Hub", out)
        self.assertLess(out.index("Zorblax"), out.index("Wibblefrump"))   # flagged (patient cue) after the main block
        line = next(l for l in out.splitlines() if "Wibblefrump" in l)
        self.assertIn("cue", line)
        self.assertTrue(line.split()[0].endswith("*"))

    def test_allow_appends_only_accepted_words_and_only_to_the_vocab_file(self):
        self.fixture()
        before = {k: v for k, v in self.tree().items()}
        out, inp, err = TTY(), TTY("a\nq\n"), io.StringIO()
        with write_guard(self.vocab), redirect_stderr(err):
            rc = V.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory),
                         "--vocab", str(self.vocab)], protector=TP, use_ner=False, out=out, inp=inp)
        self.assertEqual(rc, 0)
        self.assertEqual(self.vocab.read_text(encoding="utf-8"), "Zorblax\n")
        after = self.tree()
        after.pop("vocab.txt")
        self.assertEqual(after, before)                      # nothing else changed anywhere
        self.assertIn("Projected", out.getvalue())
        self.assert_no_words(err.getvalue())

    def test_skip_and_quit_write_nothing(self):
        rc, out, err = self.go("\nq\n")
        self.assertEqual(rc, 0)
        self.assertFalse(self.vocab.exists())
        rc, out, err = self.go("")                            # EOF = quit
        self.assertFalse(self.vocab.exists())

    def test_flagged_word_needs_the_full_word(self):
        self.fixture()
        stats = S.word_stats(self.scan())
        self.assertTrue(stats["wibblefrump"].cue)
        n = len(V.candidates(stats, 1, 200, True)[0])         # unflagged first: skip them all, then flagged
        for answers, expect in (("a\nq\n", ""), ("wibblefrump\n", "Wibblefrump\n")):
            if self.vocab.exists():
                self.vocab.unlink()
            self.n = 0
            for p in self.drop.glob("*.json"):
                p.unlink()
            rc, out, err = self.go("\n" * n + answers, "--all")
            self.assertEqual(rc, 0)
            self.assertEqual(self.vocab.read_text(encoding="utf-8") if self.vocab.exists() else "", expect)

    def test_appends_after_missing_newline_and_allowed_words_drop_off_the_list(self):
        self.vocab.write_text("Existing", encoding="utf-8")   # no trailing newline
        V.append_word(self.vocab, "Zorblax")
        self.assertEqual(self.vocab.read_text(encoding="utf-8"), "Existing\nZorblax\n")
        self.fixture()
        stats = S.word_stats(self.scan(vocab=R.load_wordlist(self.vocab)))
        self.assertNotIn("zorblax", stats)

    def test_words_go_to_the_terminal_only(self):
        rc, out, err = self.go("a\nq\n")
        self.assertIn("Zorblax", out)
        self.assert_no_words(err)
        rc, out, err = self.go("a\n", "--min-threads", "9")
        self.assert_no_words(err)

    def test_refusals(self):
        self.fixture()
        base = ["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory)]
        vault = self.td / "vault"
        vault.mkdir()
        (vault / "CLAUDE.md").write_text("# x\n\n## 8. Comms lane (x)\n", encoding="utf-8")
        cases = {
            "stdout not a terminal": (base + ["--vocab", str(self.vocab)], io.StringIO(), TTY()),
            "stdin not a terminal": (base + ["--vocab", str(self.vocab)], TTY(), io.StringIO()),
            "no vocab file": (base, TTY(), TTY()),
            "vocab in repo": (base + ["--vocab", str(G.REPO_ROOT / "v.txt")], TTY(), TTY()),
            "vocab in OneDrive": (base + ["--vocab", str(self.td / "OneDrive - T" / "v.txt")], TTY(), TTY()),
            "vocab in vault": (base + ["--vocab", str(vault / "v.txt")], TTY(), TTY()),
            "vocab in drop": (base + ["--vocab", str(self.drop / "v.txt")], TTY(), TTY()),
            "vocab folder missing": (base + ["--vocab", str(self.td / "nope" / "v.txt")], TTY(), TTY()),
            "no key store": (["--drop", str(self.drop), "--state", str(self.td / "none"), "--directory", str(self.directory),
                              "--vocab", str(self.vocab)], TTY(), TTY()),
        }
        for name, (argv, out, inp) in cases.items():
            err = io.StringIO()
            with redirect_stderr(err):
                rc = V.main(argv, protector=TP, use_ner=False, out=out, inp=inp)
            self.assertEqual(rc, 2, name)
            self.assertEqual(out.getvalue(), "", name)
            self.assert_no_words(err.getvalue())
        self.assertFalse(self.vocab.exists())
        self.assertFalse((self.td / "none").exists())

    def test_error_path_suppresses_message(self):
        self.fixture()
        err = io.StringIO()
        with mock.patch.object(S, "scan_drop", side_effect=RuntimeError("boom Zorblax")), redirect_stderr(err):
            rc = V.main(["--drop", str(self.drop), "--state", str(self.state), "--directory", str(self.directory), "--list"],
                        protector=TP, use_ner=False, out=TTY(), inp=io.StringIO())
        self.assertEqual(rc, 1)
        self.assertNotIn("Zorblax", err.getvalue())


if __name__ == "__main__":
    unittest.main()
