"""Per-category recall report for the stripper detectors. Counts only, no values.

Usage (from repo root):
    python stripper/recall.py [--n 1200] [--seed 20261027] [--ner]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "corpus"))
import detectors as D  # noqa: E402
import namegate as G  # noqa: E402
import generate as C  # noqa: E402
import ner as N  # noqa: E402

NAME_CATS = {"patient_name", "external_contact_name", "staff_name_nondirectory", "staff_name_ambiguous"}  # must never pass the gate
# Which detector codes count as the "right" detector for each label category.
COMPAT = {
    "nhs_number": {"NHS_NUMBER", "TEN_DIGITS", "DIGITS_AS_WORDS"},
    "phone": {"PHONE_UK"},
    "patient_email": {"EMAIL_EXTERNAL"},
    "external_contact_email": {"EMAIL_EXTERNAL"},
    "dob": {"DATE", "DATE_DOTTED", "MONTH_YEAR", "DOB_KEYWORD", "DOB_PARTIAL"},
    "postcode": {"POSTCODE"},
    "address": {"ADDRESS_STREET", "NER_LOCATION"},
    "mrn": {"MRN", "PATIENT_NUMBER", "LONG_DIGITS"},
    "gmc_id": {"GMC_ID", "PATIENT_NUMBER"},
    "referral_no": {"IGENE_REFERRAL", "IGENE_ID_PREFIXED"},
    "sample_no": {"IGENE_SAMPLE", "IGENE_ID_PREFIXED"},
    "worksheet_no": {"IGENE_ID_WORKSHEET"},
    "qc_no": {"IGENE_ID_QC"},
    "case_no": {"IGENE_CASE"},
    "clinical_patient_level": {"CLIN_VARIANT", "CLIN_RESULT", "CLIN_AGE", "CLIN_DX"},
    "url_personal": {"URL_PERSONAL"},
    "staff_name_ambiguous": {"NER_PERSON", "NAME"},
    "patient_name": {"NER_PERSON", "NAME"},
    "external_contact_name": {"NER_PERSON", "NAME"},
    "staff_name_nondirectory": {"NER_PERSON", "NAME"},
}


def covered_fraction(text: str, start: int, end: int, hits) -> float:
    idx = [i for i in range(start, end) if text[i].isalnum()]
    if not idx:
        return 1.0
    return sum(1 for i in idx if any(h.start <= i < h.end for h in hits)) / len(idx)


def evaluate(msgs, use_ner: bool, use_gate: bool = False):
    per = defaultdict(Counter)       # category -> counters
    adv = defaultdict(Counter)       # adversarial kind -> counters
    unlabelled = Counter()           # detector code -> hits overlapping no label
    sources = Counter()
    conflicts = 0
    extra = {"gate_reasons": Counter(), "gate_leaks": 0, "review": Counter()}
    base_people = G.parse_directory(C.directory_markdown())
    for m in msgs:
        sources[m["source"]] += 1
        conflicts += bool(m["meta"].get("conflict"))
        for fname, raw in m["fields"].items():
            text, hits = D.detect(raw)
            if use_ner:
                hits = sorted(hits + [h for h in N.detect_ner(text)
                                      if not any(h.start < x.end and x.start < h.end for x in hits)], key=lambda h: h.start)
            passed = []
            if use_gate:
                people = base_people.with_header(m["meta"].get("header_people", []))
                spans = [(h.start, h.end) for h in hits if h.code == "NER_PERSON"]
                hits = [h for h in hits if h.code != "NER_PERSON"]
                decisions, reasons = G.gate(text, spans, people, review=extra["review"])
                extra["gate_reasons"].update(reasons)
                passed = [(d.start, d.end) for d in decisions if d.action == "pass"]
                hits = sorted(hits + [D.Hit("NAME", "fail", d.start, d.end) for d in decisions if d.action == "name"
                                      and not any(d.start < x.end and x.start < d.end for x in hits)], key=lambda h: h.start)
            labels = [l for l in m["labels"] if l["field"] == fname]
            for l in labels:
                if use_gate and l["category"] in NAME_CATS and any(a < l["end"] and l["start"] < b for a, b in passed):
                    extra["gate_leaks"] += 1
            for l in labels:
                cat = l["category"]
                if l["action"] == "keep":
                    per[cat]["planted"] += 1
                    per[cat]["touched"] += any(h.start < l["end"] and l["start"] < h.end for h in hits)
                    continue
                frac = covered_fraction(text, l["start"], l["end"], hits)
                over = [h for h in hits if h.start < l["end"] and l["start"] < h.end]
                key = "full" if frac == 1.0 else "partial" if frac > 0 else "missed"
                for c in (per[cat], adv[l["kind"]] if l["adversarial"] else Counter()):
                    c["planted"] += 1
                    c[key] += 1
                if key == "full" and (cat not in COMPAT or any(h.code in COMPAT[cat] for h in over)):
                    per[cat]["correct"] += 1
                elif key == "full" and cat == "attachment_filename":
                    per[cat]["correct"] += 1
            for h in hits:
                if not any(h.start < l["end"] and l["start"] < h.end for l in labels):
                    unlabelled[h.code] += 1
    return per, adv, unlabelled, sources, conflicts, extra


def report(msgs, use_ner: bool, use_gate: bool = False) -> str:
    per, adv, unl, src, conflicts, extra = evaluate(msgs, use_ner, use_gate)
    out = [f"messages={len(msgs)} sources={dict(src)} ref+clinical_conflict_messages={conflicts}",
           f"NER layer: {N.STATUS if use_ner else 'off (deterministic only)'}; name gate: {'on' if use_gate else 'off'}", "",
           "STRIP categories (full = every character of the planted item covered)",
           f"{'category':28}{'planted':>8}{'full':>7}{'partial':>9}{'missed':>8}{'right-code':>12}{'recall%':>9}"]
    for cat in C.CATEGORIES_STRIP:
        c = per.get(cat, Counter())
        n = c["planted"]
        out.append(f"{cat:28}{n:8d}{c['full']:7d}{c['partial']:9d}{c['missed']:8d}{c['correct']:12d}"
                   f"{(100 * c['full'] / n if n else 0):9.1f}")
    out += ["", "ADVERSARIAL kinds", f"{'kind':28}{'planted':>8}{'full':>7}{'partial':>9}{'missed':>8}"]
    for k in sorted(adv):
        c = adv[k]
        out.append(f"{k:28}{c['planted']:8d}{c['full']:7d}{c['partial']:9d}{c['missed']:8d}")
    out += ["", "KEEP categories (false-strip = any detector touches the item, before the name gate exists)",
            f"{'category':28}{'planted':>8}{'touched':>9}{'false-strip%':>14}"]
    for cat in ("staff_name_directory", "staff_name_header", "config_vocab"):
        c = per.get(cat, Counter())
        out.append(f"{cat:28}{c['planted']:8d}{c['touched']:9d}{(100 * c['touched'] / c['planted'] if c['planted'] else 0):14.1f}")
    if use_gate:
        out += ["", f"NAME GATE: leaks (a patient/external/non-staff name passed) = {extra['gate_leaks']}",
                "  decisions by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(extra["gate_reasons"].items())),
                f"  distinct unknown names queued for review: {len(extra['review'])} (counts only)"]
    out += ["", "UNLABELLED detections by code (hits on text that carries no label: benign dates, decoys, etc.)"]
    out += [f"  {code:20}{n:6d}" for code, n in unl.most_common()] or ["  none"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--ner", action="store_true", help="enable the Presidio/spaCy layer if installed")
    ap.add_argument("--gate", action="store_true", help="apply the name gate (directory, headers, bare-name rules)")
    ap.add_argument("--model", default=N.MODEL, help="spaCy model for --ner")
    a = ap.parse_args()
    if a.ner:
        N.load(a.model)
    print(report(C.generate(a.n, a.seed), a.ner, a.gate))


if __name__ == "__main__":
    main()
