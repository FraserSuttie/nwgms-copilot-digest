"""Transform engine for the content-lane stripper (Comms-Intake-Design section 9, ADR 0004).

Turns detector hits and name-gate decisions into placeholders and tokens. No AI, no network.

    patient identifiers   fixed placeholders, no map          [PT-NHS] [PT-DOB] [PT-PHONE] [PT-ADDR] [PT-MRN] [PT-NUM] [PT-EMAIL]
    REF/SMP/WS/QC/CASE    truncated keyed hash, per-year key  REF-KQMTRVXZ  (decode table in the key store)
    external contacts     CONTACT-nnnn                        (header correspondents only; map in the key store)
    attachment filenames  ATT-n (type, "allowlisted label")   (original filename in the key store)
    names                 [NAME-n] per message, one-way; staff pass through via the name gate
    clinical wording      [CLIN], with three sentence-drop escalations (multiple hits, next to an
                          identifier, result-style wording)
    other                 [DATE] [GMC-ID] [URL] [LOC]

Hits with no treatment (review-severity codes and unknown iGene prefixes) are left in the text
and counted in `left`: the residual scan (next slice) must fail closed on them.

Nothing here returns or logs matched values; results carry text for the caller to file, plus counts.
"""

from __future__ import annotations

import argparse
import bisect
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "corpus"))
sys.path.insert(0, str(HERE.parent / "validator"))
import detectors as D  # noqa: E402
import keystore as K  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402

STRIPPER_VERSION = "0.2.0-transforms"

FIXED = {
    "NHS_NUMBER": "[PT-NHS]", "TEN_DIGITS": "[PT-NUM]", "DIGITS_AS_WORDS": "[PT-NUM]", "PATIENT_NUMBER": "[PT-NUM]",
    "LONG_DIGITS": "[PT-NUM]", "PHONE_UK": "[PT-PHONE]", "POSTCODE": "[PT-ADDR]", "ADDRESS_STREET": "[PT-ADDR]",
    "MRN": "[PT-MRN]", "DOB_PARTIAL": "[PT-DOB]", "GMC_ID": "[GMC-ID]", "URL_PERSONAL": "[URL]", "NER_LOCATION": "[LOC]",
}
DATE_CODES = {"DATE"}  # DATE_DOTTED / MONTH_YEAR are review-severity (may be version strings): left for the residual scan
CLIN_CODES = {"CLIN_VARIANT", "CLIN_RESULT", "CLIN_AGE", "CLIN_DX"}
RESULT_STYLE = {"CLIN_VARIANT", "CLIN_RESULT"}
ID_CATEGORY = {"IGENE_REFERRAL": "REF", "IGENE_SAMPLE": "SMP", "IGENE_ID_WORKSHEET": "WS", "IGENE_ID_QC": "QC", "IGENE_CASE": "CASE"}
PREFIX_CATEGORY = (("WS", "WS"), ("QC", "QC"), ("R", "REF"), ("S", "SMP"), ("D", "SMP"))  # IGENE_ID_PREFIXED by leading letters
DOB_CUE_DISTANCE = 40

_C = K.ALPHABET
APPROVED_RE = re.compile(
    rf"(?:REF|SMP|WS|QC|CASE)-[{_C}]{{8,16}}\b|CONTACT-\d{{4,}}|ATT-\d+"
    r"|\[(?:PT-(?:NHS|NUM|PHONE|ADDR|MRN|DOB|EMAIL)|GMC-ID|DATE|LOC|URL|CLIN|NAME-\d+)\]")

KEEP_WORDS = frozenset("""report form agenda minutes summary notes request template update spec specification guide sop draft review
timetable schedule presentation slides screenshot image export log config configuration interface mapping test plan checklist
validation change release""".split())
_KEEP_REF = re.compile(r"^(?:CRF-\d{4}-\d{3}|DOC\d{4}|v\d{1,2})$", re.I)


def blank_approved(text: str) -> str:
    """Remove approved tokens so the detectors can be re-run over the output (residual-scan preview)."""
    return APPROVED_RE.sub(" ", text)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, s = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        if m.start() > s:
            spans.append((s, m.start()))
        s = m.end()
    if len(text) > s:
        spans.append((s, len(text)))
    return spans


@dataclass
class Item:
    s: int
    e: int
    code: str
    family: str           # PT, ID, NAME, CONTACT, CLIN, OTHER
    label: str            # counting label, never a value
    fixed: str | None = None
    cat: str | None = None
    value: str = ""
    year: int = 0
    contact: tuple | None = None


@dataclass
class TextResult:
    text: str
    counts: Counter = field(default_factory=Counter)
    drops: Counter = field(default_factory=Counter)   # sentence drops by reason
    left: Counter = field(default_factory=Counter)    # hits with no treatment, by detector code
    has_id: bool = False                              # a REF/SMP/WS/QC/CASE number was present (before any drop)
    has_result_style: bool = False                    # result-style clinical wording was present


@dataclass
class MessageResult:
    fields: dict
    counts: Counter
    drops: Counter
    left: Counter
    has_id: bool
    has_result_style: bool
    msg_hash: str


class Transformer:
    def __init__(self, store: K.KeyStore, people: G.People, ignore=frozenset(), review: Counter | None = None,
                 use_ner: bool = False, year: int | None = None):
        self.store, self.people, self.ignore, self.review, self.use_ner = store, people, ignore, review, use_ner
        self.year = year or datetime.now(timezone.utc).year

    # ---- one string -----------------------------------------------------------------------
    def text(self, raw: str, header_people=()) -> TextResult:
        text, hits = D.detect(raw)
        if self.use_ner:
            extra = [h for h in N.detect_ner(text) if not any(h.start < x.end and x.start < h.end for x in hits)]
            hits = sorted(hits + extra, key=lambda h: h.start)
        ext_emails, ext_names = {}, {}
        for display, addr in header_people:
            if not self._trusted(addr):
                n = self.store.contact(addr, display)
                ext_emails[addr.strip().lower()] = n
                if G.norm(display):
                    ext_names[G.norm(display)] = n
        people = self.people.with_header(header_people)
        spans = [(h.start, h.end) for h in hits if h.code == "NER_PERSON"]
        hits = [h for h in hits if h.code != "NER_PERSON"]
        decisions, _ = G.gate(text, spans, people, ignore=self.ignore, review=self.review)

        res = TextResult(text)
        cues = [h for h in hits if h.code == "DOB_KEYWORD"]
        items: list[Item] = []
        for h in hits:
            it = self._item(text, h, cues, ext_emails)
            if it:
                items.append(it)
            else:
                res.left[h.code] += 1
            res.has_result_style |= h.code in RESULT_STYLE
        for d in decisions:
            if d.action != "name" or any(d.start < x.end and x.start < d.end for x in hits):
                continue
            n = ext_names.get(G.norm(text[d.start:d.end]))
            if n is not None:
                items.append(Item(d.start, d.end, "NAME", "CONTACT", "CONTACT", contact=("", "", n)))
            else:
                items.append(Item(d.start, d.end, "NAME", "NAME", "NAME", fixed=f"[NAME-{d.n}]"))
        items.sort(key=lambda i: i.s)
        order = {}  # keep [NAME-n] contiguous even when the gate numbered a name that became a CONTACT
        for it in items:
            if it.family == "NAME":
                gate_n = int(it.fixed[6:-1])
                it.fixed = f"[NAME-{order.setdefault(gate_n, len(order) + 1)}]"
        res.has_id = any(i.family == "ID" for i in items)
        items = self._escalate(text, items, res)

        out, pos = [], 0
        for it in items:
            out.append(text[pos:it.s])
            out.append(self._realise(it))
            res.counts[it.label] += 1
            pos = it.e
        out.append(text[pos:])
        res.text = "".join(out)
        return res

    @staticmethod
    def _trusted(addr: str) -> bool:
        dom = addr.rsplit("@", 1)[-1].lower()
        return any(dom == d or dom.endswith("." + d) for d in G.P.ALLOWED_EMAIL_DOMAINS)

    def _item(self, text: str, h, cues, ext_emails) -> Item | None:
        c, s, e = h.code, h.start, h.end
        if c in FIXED:
            return Item(s, e, c, "PT" if c.startswith(("NHS", "TEN", "DIGITS", "PHONE", "POSTCODE", "ADDRESS", "MRN", "DOB", "PATIENT", "LONG")) else "OTHER",
                        FIXED[c].strip("[]"), fixed=FIXED[c])
        if c in DATE_CODES:
            near = any(0 <= s - q.end <= DOB_CUE_DISTANCE for q in cues)
            return Item(s, e, c, "PT" if near else "OTHER", "PT-DOB" if near else "DATE", fixed="[PT-DOB]" if near else "[DATE]")
        if c == "EMAIL_EXTERNAL":
            n = ext_emails.get(text[s:e].strip().lower())
            if n is not None:
                return Item(s, e, c, "CONTACT", "CONTACT", contact=("", "", n))
            return Item(s, e, c, "PT", "PT-EMAIL", fixed="[PT-EMAIL]")
        if c in CLIN_CODES:
            return Item(s, e, c, "CLIN", "CLIN", fixed="[CLIN]")
        cat = ID_CATEGORY.get(c)
        if c == "IGENE_ID_PREFIXED":
            v = K.normalise_value(text[s:e])
            cat = next((cat for pre, cat in PREFIX_CATEGORY if v.startswith(pre) and v[len(pre):len(pre) + 2].isdigit()), None)
        if cat:
            v = K.normalise_value(text[s:e])
            m = re.match(r"[A-Z]{1,2}(\d{2})", v)
            year = 2000 + int(m.group(1)) if m and cat != "CASE" else self.year
            return Item(s, e, c, "ID", cat, cat=cat, value=v, year=year)
        return None

    def _escalate(self, text: str, items: list[Item], res: TextResult) -> list[Item]:
        sents = sentence_spans(text)
        starts = [a for a, _ in sents]
        by = defaultdict(list)
        for it in items:
            by[max(0, bisect.bisect_right(starts, it.s) - 1)].append(it)
        drops = []
        for i, its in by.items():
            clin = [x for x in its if x.family == "CLIN"]
            if not clin:
                continue
            ident = [x for x in its if x.family in ("PT", "ID", "NAME", "CONTACT")]
            reason = "multi" if len(clin) >= 2 else "ident" if ident else "result" if clin[0].code in RESULT_STYLE else None
            if reason:
                drops.append([sents[i][0], sents[i][1], reason])
        if not drops:
            return items
        merged = []
        for d in sorted(drops):
            for it in items:  # widen to any hit that straddles the sentence edge
                if it.s < d[1] and d[0] < it.e:
                    d[0], d[1] = min(d[0], it.s), max(d[1], it.e)
            if merged and d[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], d[1])
            else:
                merged.append(d)
            res.drops[d[2]] += 1
        kept = [it for it in items if not any(it.s < b and a < it.e for a, b, _ in merged)]
        kept += [Item(a, b, "CLIN_DROP", "CLIN", "CLIN", fixed="[CLIN]") for a, b, _ in merged]
        return sorted(kept, key=lambda i: i.s)

    def _realise(self, it: Item) -> str:
        if it.fixed:
            return it.fixed
        if it.cat:
            return self.store.token(it.cat, it.value, it.year)
        return f"CONTACT-{it.contact[2]:04d}"

    # ---- one message ----------------------------------------------------------------------
    def attachment(self, filename: str, msg_hash: str, n: int) -> str:
        stem, _, ext = filename.rpartition(".")
        if not stem:
            stem, ext = filename, ""
        ext = ext.lower() if re.fullmatch(r"[A-Za-z0-9]{1,5}", ext) else "file"
        words = [w for w in re.split(r"[\s_]+", stem) if w and (w.lower() in KEEP_WORDS or _KEEP_REF.match(w))]
        self.store.attachment(msg_hash, n, filename)
        label = f', "{" ".join(words)}"' if words else ""
        return f"ATT-{n} ({ext}{label})"

    def message(self, fields: dict, header_people=(), commit: bool = True, audit: bool = True) -> MessageResult:
        msg_hash = self.store.message_hash("\x1f".join(f"{k}\x1e{v}" for k, v in sorted(fields.items())))
        out, counts, drops, left = {}, Counter(), Counter(), Counter()
        has_id = has_rs = False
        att = 0
        for name, raw in fields.items():
            if name.startswith("attachment_"):
                att += 1
                out[name] = self.attachment(raw, msg_hash, att)
                counts["ATT"] += 1
                continue
            r = self.text(raw, header_people)
            out[name] = r.text
            counts.update(r.counts); drops.update(r.drops); left.update(r.left)
            has_id |= r.has_id; has_rs |= r.has_result_style
        if audit:
            self.store.audit(msg_hash, "stripped", counts, STRIPPER_VERSION)
        if commit:
            self.store.save()
        return MessageResult(out, counts, drops, left, has_id, has_rs, msg_hash)


# ---- corpus run (synthetic only): counts, leaks, residual preview ---------------------------------
LEAK_CATS = ["nhs_number", "dob", "phone", "address", "postcode", "patient_email", "external_contact_email", "mrn",
             "referral_no", "sample_no", "worksheet_no", "qc_no", "case_no", "gmc_id", "url_personal",
             "clinical_patient_level", "attachment_filename"]


def run_corpus(msgs, tr: Transformer) -> dict:
    import unicodedata
    stats = {"messages": len(msgs), "counts": Counter(), "drops": Counter(), "left": Counter(), "leaks": Counter(),
             "planted": Counter(), "residual": Counter(), "outputs": {}, "dropped_sentences_with_result_only": 0}
    for m in msgs:
        r = tr.message(m["fields"], m["meta"].get("header_people", []), commit=False, audit=False)
        stats["outputs"][m["id"]] = r
        stats["counts"].update(r.counts); stats["drops"].update(r.drops); stats["left"].update(r.left)
        for l in m["labels"]:
            if l["category"] in LEAK_CATS and l["action"] == "strip":
                stats["planted"][l["category"]] += 1
                planted = unicodedata.normalize("NFKC", m["fields"][l["field"]][l["start"]:l["end"]])
                if planted.strip() and planted in unicodedata.normalize("NFKC", r.fields[l["field"]]):
                    stats["leaks"][l["category"]] += 1
        for text in r.fields.values():
            _, hits = D.detect(blank_approved(text))
            for h in hits:
                stats["residual"][f"{h.code}/{h.severity}"] += 1
    return stats


def main() -> None:
    import generate as C
    ap = argparse.ArgumentParser(description="Synthetic corpus transform report (counts only, throwaway test store)")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--ner", action="store_true")
    a = ap.parse_args()
    if a.ner:
        N.load()
    msgs = C.generate(a.n, a.seed)
    with tempfile.TemporaryDirectory() as td:
        st = K.KeyStore(Path(td) / "ks", K.TestOnlyProtector())
        tr = Transformer(st, G.parse_directory(C.directory_markdown()), use_ner=a.ner, year=2026)
        s = run_corpus(msgs, tr)
        toks = {c: len(t) for c, t in st.t["codes"].items()}
        longer = sum(1 for t in st.t["codes"].values() for code in t.values() if len(code) > K.START_LEN)
    print(f"messages={s['messages']} NER: {N.STATUS if a.ner else 'off'} version={STRIPPER_VERSION} (synthetic, throwaway store)")
    print("\ntreatments applied:"); [print(f"  {k:12}{v:6d}") for k, v in sorted(s["counts"].items())]
    print("\nsentence drops by reason:", dict(s["drops"]))
    print("hits left untreated (residual scan must catch):", dict(s["left"]) or "none")
    print("\nplanted strip items still present verbatim in output (leaks):")
    for c in LEAK_CATS:
        print(f"  {c:26}{s['leaks'][c]:4d} / {s['planted'][c]}")
    print("\nresidual preview (detectors re-run over output, approved tokens blanked):")
    print("  " + (", ".join(f"{k}={v}" for k, v in sorted(s['residual'].items())) or "none"))
    print(f"\ntokens issued: {toks}; collisions resolved by a longer code: {longer}")


if __name__ == "__main__":
    main()
