"""Per-message pipeline: eligibility, transform, residual scan, density check, quarantine (ADR 0005).

    outcome = Pipeline(transformer, quarantine).process(fields, header_people[, check])

`outcome.status` is "ok" (then `outcome.fields` is the stripped message) or "quarantined" (then
`outcome.fields` is None: there is NEVER partial output). Reason codes are counts only.

Order: body size -> transform -> eligibility (result-style wording AND an identifier) -> residual scan
-> density check -> caps policy (ADR 0017: a message held ONLY for unknown capitalised words is released when the
classifier finds no name signal on any of them; NER must be active; the word count goes on the block). Any reason, or any unexpected exception, sends the whole raw message to quarantine.
If the quarantine write itself fails, QuarantineError propagates and nothing is released.

Corpus run (synthetic only, throwaway stores, counts only):
    python stripper/pipeline.py [--n 1200] [--seed 20261027] [--ner]
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "corpus"))
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402

VERSION = T.STRIPPER_VERSION


@dataclass
class Outcome:
    status: str                                   # "ok" | "quarantined"
    fields: dict | None                           # stripped fields, only when ok
    reasons: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)
    msg_hash: str = ""


@dataclass
class Evaluation:
    """Pure result of the checks (ADR 0010). `stripped` is the transformed attempt, held in memory only: it is None when the
    message never reached the transform (ELIG_SIZE) or an unexpected exception occurred. Never stored or logged."""
    reasons: Counter
    counts: Counter
    stripped: dict | None
    allowed: frozenset
    msg_hash: str


class Pipeline:
    def __init__(self, tr: T.Transformer, quarantine: Q.Quarantine, limits: R.Limits = R.Limits(), vocab=frozenset()):
        if tr.use_ner and not str(N.STATUS).startswith("active"):
            raise RuntimeError("NER was requested but the layer is not active; refusing to run without it")
        self.tr, self.q, self.limits, self.vocab = tr, quarantine, limits, frozenset(vocab)

    def evaluate(self, fields: dict, header_people=(), check=None) -> Evaluation:
        """Every check, no writes: no quarantine, no audit, no key-store save. `process` is this plus the decision."""
        tr, lim = self.tr, self.limits
        msg_hash = tr.hash_fields(fields)
        reasons: Counter = Counter()
        counts: Counter = Counter()
        stripped = None
        allowed: frozenset = frozenset()
        try:
            body = [v for k, v in fields.items() if not k.startswith("attachment_")]
            if sum(len(v) for v in body) > lim.max_chars:
                reasons["ELIG_SIZE"] += 1
            else:
                r = tr.message(fields, header_people, commit=False, audit=False)
                counts = r.counts
                stripped = r.fields
                if r.has_id and r.has_result_style:
                    reasons["ELIG_RESULT_ID"] += 1
                people = tr.people.with_header(header_people)
                allowed = R.allowed_words(people, tr.ignore, self.vocab, header_people)
                caps_total = 0
                for name, text in r.fields.items():
                    if name.startswith("attachment_"):
                        reasons.update(R.scan_attachment(text, r.emitted))
                    else:
                        reasons.update(R.scan(text, r.emitted, people, tr.ignore, tr.use_ner, allowed))
                        reasons.update(R.density(text, allowed, lim, caps=lim.unknown_caps == 1))
                        caps_total += R.unknown_caps(text, allowed)
                        reasons.update(R.cue_check(text, allowed))
                if lim.unknown_caps > 1 and caps_total >= lim.unknown_caps:
                    reasons["DENS_CAPS"] += 1
                if lim.caps_policy == "B" and tr.use_ner and set(reasons) == {"DENS_CAPS"}:
                    pol = R.caps_policy(r.fields, allowed, True)      # ADR 0017: release and flag, NER required
                    if pol["release_b"]:
                        del reasons["DENS_CAPS"]
                        counts = counts.copy()
                        counts["CAPS_RELEASED"] = pol["words"]
                if not reasons and check is not None:
                    reasons.update(check(r.fields))
        except Exception:  # noqa: BLE001 - fail closed on anything unexpected
            reasons["PIPELINE_ERROR"] += 1
            stripped = None
        return Evaluation(reasons, counts, stripped, allowed, msg_hash)

    def process(self, fields: dict, header_people=(), check=None) -> Outcome:
        """`check(stripped_fields) -> Counter` is an optional front-end shape check; any reason it returns quarantines."""
        tr = self.tr
        ev = self.evaluate(fields, header_people, check)
        reasons, msg_hash = ev.reasons, ev.msg_hash
        out_fields = None if reasons else ev.stripped
        if reasons or out_fields is None:
            if not reasons:
                reasons["PIPELINE_ERROR"] += 1
            message = {"fields": fields, "header_people": [list(p) for p in header_people]}
            self.q.put(msg_hash, message, reasons, VERSION)   # QuarantineError propagates: nothing is released
            tr.store.audit(msg_hash, "quarantined", reasons, VERSION)
            tr.store.save()
            return Outcome("quarantined", None, reasons, ev.counts, msg_hash)
        tr.store.audit(msg_hash, "ok", ev.counts, VERSION)
        tr.store.save()
        return Outcome("ok", out_fields, reasons, ev.counts, msg_hash)


# ---- corpus run (synthetic only) --------------------------------------------------------------------
NAME_CATS = {"patient_name", "external_contact_name", "staff_name_nondirectory", "staff_name_ambiguous"}


def name_fragments_surviving(msg: dict, out_fields: dict) -> Counter:
    """Planted name tokens (3+ letters) still present in the output, by label kind. Counts only.
    A token that is also a kept staff name in the same message is not counted (a colliding staff greeting is correct)."""
    c = Counter()
    kept = set()   # tokens that legitimately appear as staff names (directory/header) in this message
    for l in msg["labels"]:
        if l["action"] == "keep" and l["category"].startswith("staff_name"):
            kept.update(re.findall(r"[A-Za-z]{3,}", msg["fields"][l["field"]][l["start"]:l["end"]]))
    for l in msg["labels"]:
        if l["category"] in NAME_CATS and not l["field"].startswith("attachment_"):
            planted = unicodedata.normalize("NFKC", msg["fields"][l["field"]][l["start"]:l["end"]])
            toks = [t for t in re.findall(r"[A-Za-z]{3,}", planted) if t not in kept]
            if any(t in out_fields[l["field"]] for t in toks):
                c[l["kind"]] += 1
    return c


def initials_surviving(msg: dict, out_fields: dict) -> Counter:
    """Planted names with no 3-letter token (initials such as 'M.A.' or 'MA') still present verbatim in the output, by
    kind. name_fragments_surviving cannot see these. Counts only."""
    c = Counter()
    for l in msg["labels"]:
        if l["category"] in NAME_CATS and not l["field"].startswith("attachment_"):
            planted = unicodedata.normalize("NFKC", msg["fields"][l["field"]][l["start"]:l["end"]]).strip()
            if planted and not re.search(r"[A-Za-z]{3,}", planted) and re.search(
                    r"(?<![A-Za-z])" + re.escape(planted) + r"(?![A-Za-z])", out_fields[l["field"]]):
                c[l["kind"]] += 1
    return c


def corpus_vocab() -> frozenset:
    import vocab as V
    # Stand-in for Fraser's local vocabulary file / review-queue ignore list: system and domain words that are
    # not names. The first corpus run showed these four words caused every clean-message density quarantine.
    words = {"Epic", "Referring", "Test", "QC"}
    for term in V.CONFIG_VOCAB:
        words.update(re.findall(r"[A-Za-z0-9']+", term))
    return frozenset(words)


def run_corpus(msgs, pipe: Pipeline, leak_cats=None) -> dict:
    leak_cats = T.LEAK_CATS if leak_cats is None else leak_cats
    s = {"messages": len(msgs), "ok": 0, "quarantined": 0, "reasons": Counter(), "reason_msgs": Counter(),
         "ok_name_msgs": 0, "ok_name_kinds": Counter(), "q_clean": 0, "q_leaky": 0, "leaky": 0, "ok_leaks": Counter(),
         "planted": Counter(), "ok_initials_msgs": 0, "ok_initials_kinds": Counter(), "ok_name_by_kind_msgs": Counter()}
    for m in msgs:
        o = pipe.process(m["fields"], m["meta"].get("header_people", []))
        sur = Counter()
        if o.status == "ok":
            s["ok"] += 1
            sur = name_fragments_surviving(m, o.fields)
            if sur:
                s["ok_name_msgs"] += 1
                s["ok_name_kinds"].update(sur)
            ini = initials_surviving(m, o.fields)
            if ini:
                s["ok_initials_msgs"] += 1
                s["ok_initials_kinds"].update(ini)
            for l in m["labels"]:
                if l["category"] in leak_cats and l["action"] == "strip":
                    planted = unicodedata.normalize("NFKC", m["fields"][l["field"]][l["start"]:l["end"]])
                    if planted.strip() and planted in unicodedata.normalize("NFKC", o.fields[l["field"]]):
                        s["ok_leaks"][l["category"]] += 1
        else:
            s["quarantined"] += 1
            s["reasons"].update(o.reasons)
            s["reason_msgs"].update(o.reasons.keys())
    return s


def caps_policy_corpus(msgs, pipe: Pipeline) -> dict:
    """Draft ADR 0017 measure (no writes): messages held for DENS_CAPS only, and what policies A and B would release,
    with planted names or initials still present in those releases. Counts only."""
    s = {"caps_only": 0, "a": 0, "b": 0, "a_names": Counter(), "b_names": Counter(), "a_msgs": 0, "b_msgs": 0,
         "tiers": Counter(), "signals": Counter()}
    for m in msgs:
        ev = pipe.evaluate(m["fields"], m["meta"].get("header_people", []))
        if ev.stripped is None or set(ev.reasons) != {"DENS_CAPS"}:
            continue
        s["caps_only"] += 1
        pol = R.caps_policy(ev.stripped, ev.allowed, pipe.tr.use_ner)
        s["tiers"].update(pol["tiers"])
        s["signals"].update(pol["signals"])
        leaked = name_fragments_surviving(m, ev.stripped) + initials_surviving(m, ev.stripped)
        for key in ("a", "b"):
            if pol["release_" + key]:
                s[key] += 1
                if leaked:
                    s[key + "_msgs"] += 1
                    s[key + "_names"].update(leaked)
    return s


def print_caps_policy(label: str, s: dict) -> None:
    print(f"{label}: held for DENS_CAPS only {s['caps_only']}; word tiers {dict(sorted(s['tiers'].items()))}")
    print(f"  policy A (all words FLAG) would release {s['a']}, with a planted name or initials: {s['a_msgs']} "
          f"{dict(s['a_names']) or ''}")
    print(f"  policy B (no word HOLD) would release {s['b']}, with a planted name or initials: {s['b_msgs']} "
          f"{dict(s['b_names']) or ''}")
    print("  signals:", dict(sorted(s["signals"].items())))


def main() -> None:
    import generate as C
    ap = argparse.ArgumentParser(description="Synthetic corpus through the full pipeline (counts only, throwaway stores)")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--ner", action="store_true")
    ap.add_argument("--caps-policy", action="store_true", help="draft ADR 0017 classifier measure (no behaviour change)")
    a = ap.parse_args()
    if a.ner:
        N.load()
    msgs = C.generate(a.n, a.seed)
    with tempfile.TemporaryDirectory() as td:
        st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
        q = Q.Quarantine(Path(td) / "ks", K.TestOnlyProtector())
        tr = T.Transformer(st, G.parse_directory(C.directory_markdown()), use_ner=a.ner, year=2026)
        pipe = Pipeline(tr, q, vocab=corpus_vocab())
        s = run_corpus(msgs, pipe)
        cp = caps_policy_corpus(msgs, pipe) if a.caps_policy else None
    n = s["messages"]
    print(f"messages={n} NER: {N.STATUS if a.ner else 'off'} version={VERSION} (synthetic, throwaway stores)")
    print(f"ok={s['ok']}  quarantined={s['quarantined']} ({100 * s['quarantined'] / n:.1f}%)")
    print("quarantined messages by reason (a message can have several):", dict(sorted(s["reason_msgs"].items())))
    print("reason-code counts:", dict(sorted(s["reasons"].items())))
    print(f"\nEND TO END, released (ok) messages with a planted name fragment still present: {s['ok_name_msgs']} of {s['ok']}  by kind: {dict(s['ok_name_kinds'])}")
    print("released messages with a planted strip item still present verbatim:", dict(s["ok_leaks"]) or "none")
    print(f"released messages with planted initials still present (not counted above): {s['ok_initials_msgs']} of {s['ok']}"
          f"  by kind: {dict(s['ok_initials_kinds'])}")
    # Regression set (session 11 leak read; ADR 0012 to 0014): own seed, own throwaway stores, Directory with externals.
    # The fixed kinds must release nothing; the LIMITATION kinds (un-cued patient names, A2) are measured, not asserted.
    reg = C.regression()
    fixed = [m for m in reg if any(l["kind"] in C.REGRESSION_KINDS for l in m["labels"])]
    lim = [m for m in reg if any(l["kind"] in C.LIMITATION_KINDS for l in m["labels"])]
    with tempfile.TemporaryDirectory() as td:
        st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
        q = Q.Quarantine(Path(td) / "ks", K.TestOnlyProtector())
        tr = T.Transformer(st, G.parse_directory(C.regression_directory_markdown()), use_ner=a.ner, year=2026)
        pipe = Pipeline(tr, q, vocab=corpus_vocab())
        rf = run_corpus(fixed, pipe, T.LEAK_CATS + C.CATEGORIES_REGRESSION)
        rl = run_corpus(lim, pipe, T.LEAK_CATS + C.CATEGORIES_REGRESSION)
        rcp = caps_policy_corpus(fixed + lim, pipe) if a.caps_policy else None
    print(f"\nREGRESSION set (Run 10 leak read; ADR 0012 to 0014): fixed kinds, messages={rf['messages']} ok={rf['ok']}"
          f" quarantined={rf['quarantined']}")
    print("  quarantined messages by reason:", dict(sorted(rf["reason_msgs"].items())) or "none")
    print(f"  released with a planted name fragment: {rf['ok_name_msgs']}; with planted initials: {rf['ok_initials_msgs']};"
          " with a planted strip item verbatim:", dict(rf["ok_leaks"]) or "none")
    print(f"LIMITATION kinds (A2, un-cued patient names): messages={rl['messages']} ok={rl['ok']} quarantined={rl['quarantined']}")
    print("  quarantined messages by reason:", dict(sorted(rl["reason_msgs"].items())) or "none")
    print(f"  released with a lowercase name: {dict(rl['ok_name_kinds']) or 0}; with initials: {dict(rl['ok_initials_kinds']) or 0}")
    if cp is not None:
        print("\nCAPS CLASSIFIER (ADR 0017; with NER on, policy B is already applied in the counts above; these are the messages still held for DENS_CAPS only)")
        print_caps_policy("MAIN corpus", cp)
        print_caps_policy("REGRESSION + LIMITATION sets", rcp)



if __name__ == "__main__":
    main()
