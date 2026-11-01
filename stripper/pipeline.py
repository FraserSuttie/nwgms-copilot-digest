"""Per-message pipeline: eligibility, transform, residual scan, density check, quarantine (ADR 0005).

    outcome = Pipeline(transformer, quarantine).process(fields, header_people)

`outcome.status` is "ok" (then `outcome.fields` is the stripped message) or "quarantined" (then
`outcome.fields` is None: there is NEVER partial output). Reason codes are counts only.

Order: body size -> transform -> eligibility (result-style wording AND an identifier) -> residual scan
-> density check. Any reason, or any unexpected exception, sends the whole raw message to quarantine.
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


class Pipeline:
    def __init__(self, tr: T.Transformer, quarantine: Q.Quarantine, limits: R.Limits = R.Limits(), vocab=frozenset()):
        if tr.use_ner and not str(N.STATUS).startswith("active"):
            raise RuntimeError("NER was requested but the layer is not active; refusing to run without it")
        self.tr, self.q, self.limits, self.vocab = tr, quarantine, limits, frozenset(vocab)

    def process(self, fields: dict, header_people=()) -> Outcome:
        tr, lim = self.tr, self.limits
        msg_hash = tr.hash_fields(fields)
        reasons: Counter = Counter()
        counts: Counter = Counter()
        out_fields = None
        try:
            body = [v for k, v in fields.items() if not k.startswith("attachment_")]
            if sum(len(v) for v in body) > lim.max_chars:
                reasons["ELIG_SIZE"] += 1
            else:
                r = tr.message(fields, header_people, commit=False, audit=False)
                counts = r.counts
                if r.has_id and r.has_result_style:
                    reasons["ELIG_RESULT_ID"] += 1
                people = tr.people.with_header(header_people)
                allowed = R.allowed_words(people, tr.ignore, self.vocab, header_people)
                for name, text in r.fields.items():
                    if name.startswith("attachment_"):
                        reasons.update(R.scan_attachment(text, r.emitted))
                    else:
                        reasons.update(R.scan(text, r.emitted, people, tr.ignore, tr.use_ner, allowed))
                        reasons.update(R.density(text, allowed, lim))
                if not reasons:
                    out_fields = r.fields
        except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
            reasons["PIPELINE_ERROR"] += 1
            out_fields = None
        if reasons or out_fields is None:
            if not reasons:
                reasons["PIPELINE_ERROR"] += 1
            message = {"fields": fields, "header_people": [list(p) for p in header_people]}
            self.q.put(msg_hash, message, reasons, VERSION)   # QuarantineError propagates: nothing is released
            tr.store.audit(msg_hash, "quarantined", reasons, VERSION)
            tr.store.save()
            return Outcome("quarantined", None, reasons, counts, msg_hash)
        tr.store.audit(msg_hash, "ok", counts, VERSION)
        tr.store.save()
        return Outcome("ok", out_fields, reasons, counts, msg_hash)


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


def corpus_vocab() -> frozenset:
    import vocab as V
    # Stand-in for Fraser's local vocabulary file / review-queue ignore list: system and domain words that are
    # not names. The first corpus run showed these four words caused every clean-message density quarantine.
    words = {"Epic", "Referring", "Test", "QC"}
    for term in V.CONFIG_VOCAB:
        words.update(re.findall(r"[A-Za-z0-9']+", term))
    return frozenset(words)


def run_corpus(msgs, pipe: Pipeline) -> dict:
    s = {"messages": len(msgs), "ok": 0, "quarantined": 0, "reasons": Counter(), "reason_msgs": Counter(),
         "ok_name_msgs": 0, "ok_name_kinds": Counter(), "q_clean": 0, "q_leaky": 0, "leaky": 0, "ok_leaks": Counter(),
         "planted": Counter()}
    for m in msgs:
        o = pipe.process(m["fields"], m["meta"].get("header_people", []))
        sur = Counter()
        if o.status == "ok":
            s["ok"] += 1
            sur = name_fragments_surviving(m, o.fields)
            if sur:
                s["ok_name_msgs"] += 1
                s["ok_name_kinds"].update(sur)
            for l in m["labels"]:
                if l["category"] in T.LEAK_CATS and l["action"] == "strip":
                    planted = unicodedata.normalize("NFKC", m["fields"][l["field"]][l["start"]:l["end"]])
                    if planted.strip() and planted in unicodedata.normalize("NFKC", o.fields[l["field"]]):
                        s["ok_leaks"][l["category"]] += 1
        else:
            s["quarantined"] += 1
            s["reasons"].update(o.reasons)
            s["reason_msgs"].update(o.reasons.keys())
    return s


def main() -> None:
    import generate as C
    ap = argparse.ArgumentParser(description="Synthetic corpus through the full pipeline (counts only, throwaway stores)")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--ner", action="store_true")
    a = ap.parse_args()
    if a.ner:
        N.load()
    msgs = C.generate(a.n, a.seed)
    with tempfile.TemporaryDirectory() as td:
        st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
        q = Q.Quarantine(Path(td) / "ks", K.TestOnlyProtector())
        tr = T.Transformer(st, G.parse_directory(C.directory_markdown()), use_ner=a.ner, year=2026)
        s = run_corpus(msgs, Pipeline(tr, q, vocab=corpus_vocab()))
    n = s["messages"]
    print(f"messages={n} NER: {N.STATUS if a.ner else 'off'} version={VERSION} (synthetic, throwaway stores)")
    print(f"ok={s['ok']}  quarantined={s['quarantined']} ({100 * s['quarantined'] / n:.1f}%)")
    print("quarantined messages by reason (a message can have several):", dict(sorted(s["reason_msgs"].items())))
    print("reason-code counts:", dict(sorted(s["reasons"].items())))
    print(f"\nEND TO END, released (ok) messages with a planted name fragment still present: {s['ok_name_msgs']} of {s['ok']}  by kind: {dict(s['ok_name_kinds'])}")
    print("released messages with a planted strip item still present verbatim:", dict(s["ok_leaks"]) or "none")


if __name__ == "__main__":
    main()
