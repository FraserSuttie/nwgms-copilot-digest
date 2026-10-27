"""Synthetic corpus generator for the content-lane stripper (Test Plan section 1).

Every planted item is recorded as a label: field, category, start, end, action
("strip" or "keep"), and whether it is adversarial. Output is JSONL in
stripper/corpus/out/ (git-ignored) and is reproducible from the seed.

All data is invented. Identifier-shaped values are assembled at runtime from parts, and
NHS-style numbers reuse validator/tests/generators.py (999 prefix, valid mod-11).

Usage (from repo root):
    python stripper/corpus/generate.py --n 1200 --seed 20261027
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validator" / "tests"))
import generators as VG  # noqa: E402
import vocab as V  # noqa: E402

OUT_DIR = HERE / "out"
B34 = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

CATEGORIES_STRIP = [
    "patient_name", "dob", "nhs_number", "phone", "address", "postcode", "patient_email", "mrn",
    "referral_no", "sample_no", "worksheet_no", "qc_no", "case_no", "gmc_id",
    "external_contact_name", "external_contact_email", "staff_name_nondirectory", "staff_name_ambiguous",
    "attachment_filename", "clinical_patient_level", "url_personal",
]
CATEGORIES_KEEP = ["staff_name_directory", "staff_name_header", "config_vocab", "trust_email"]


@dataclass
class Builder:
    """Accumulates text for one field and records labelled spans."""
    text: str = ""
    labels: list = field(default_factory=list)
    fname: str = "body"

    def add(self, s: str) -> "Builder":
        self.text += s
        return self

    def plant(self, category: str, value: str, adversarial: str = "") -> "Builder":
        start = len(self.text)
        self.text += value
        action = "keep" if category in CATEGORIES_KEEP else "strip"
        self.labels.append({"field": self.fname, "category": category, "start": start,
                            "end": len(self.text), "action": action,
                            "adversarial": bool(adversarial), "kind": adversarial or "plain"})
        return self


class Gen:
    def __init__(self, seed: int):
        self.r = random.Random(seed)

    # -- value makers (assembled from parts, never complete literals) -------
    def nhs(self) -> str:
        return VG.nhs_digits(self.r.randint(100000, 899999))

    def nhs_formatted(self) -> str:
        d = self.nhs()
        return self.r.choice([f"{d[:3]} {d[3:6]} {d[6:]}", d, f"{d[:3]}-{d[3:6]}-{d[6:]}"])

    def phone(self) -> str:
        r = self.r
        a, b, c = r.randint(0, 9), r.randint(0, 9), r.randint(0, 9)
        n = f"{a}{b}{c}"
        kind = r.choice(["mobile", "mobile_c", "land", "london", "intl", "manc"])
        if kind == "mobile":
            return "0" + "7700 900" + n
        if kind == "mobile_c":
            return "0" + "7700900" + n
        if kind == "land":
            return "01" + "632 960" + n
        if kind == "london":
            return "020 " + "7946 0" + n
        if kind == "intl":
            return "+44 " + "7700 900" + n
        return "0161 " + "496 0" + n

    def phone_bracket(self) -> str:
        return "(0161) 496 0" + f"{self.r.randint(100, 999)}"

    def dob(self) -> str:
        r = self.r
        d, m, y = r.randint(1, 28), r.randint(1, 12), r.randint(1935, 2018)
        months = ["January", "February", "March", "April", "May", "June", "July", "August",
                  "September", "October", "November", "December"]
        return r.choice([f"{d:02d}/{m:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{d} {months[m-1]} {y}",
                         f"{months[m-1]} {d}, {y}", f"{d:02d}-{m:02d}-{y}"])

    def postcode(self) -> str:
        r = self.r
        return f"ZZ{r.randint(1, 9)} {r.randint(1, 9)}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}"

    def address(self) -> str:
        r = self.r
        flat = f"Flat {r.randint(1, 20)}, " if r.random() < 0.3 else ""
        return f"{flat}{r.randint(1, 140)} {r.choice(V.STREETS)} {r.choice(V.STREET_TYPES)}"

    def _id(self, prefix: str, nb34: int) -> str:
        return prefix + "26" + "-" + "".join(self.r.choice(B34) for _ in range(nb34))

    def referral(self) -> str: return self._id("R", 4)
    def sample(self) -> str: return self._id(self.r.choice(["S", "D"]), 4)
    def worksheet(self) -> str: return "WS" + "26" + "-" + f"{self.r.randint(1, 99999):05d}"
    def qc(self) -> str: return "QC" + "26" + "-" + f"{self.r.randint(1, 9999):04d}"
    def case(self) -> str: return "2026" + "/" + f"{self.r.randint(1, 9999):04d}"

    def mrn(self) -> tuple[str, str]:
        """Return (value, kind). Shapes from Fraser: ODS(3 letters)+7 digits, or 8 digits."""
        if self.r.random() < 0.65:
            ods = self.r.choice(["XQA", "XQB", "XQC", "XQD"])  # invented, not real ODS codes
            return ods + "".join(str(self.r.randint(0, 9)) for _ in range(7)), "ods7"
        return "".join(str(self.r.randint(0, 9)) for _ in range(8)), "digits8"

    def gmc(self) -> str:
        return "".join(str(self.r.randint(0, 9)) for _ in range(7))

    def pt_name(self) -> tuple[str, str, str]:
        f, l = self.r.choice(V.PATIENT_FIRST), self.r.choice(V.PATIENT_LAST)
        return f, l, f"{f} {l}"

    def ext_name(self) -> str:
        return f"{self.r.choice(V.EXT_FIRST)} {self.r.choice(V.EXT_LAST)}"

    def ext_email(self, name: str) -> str:
        f, l = name.lower().replace("-", "").split()
        return f"{f}.{l}" + "@" + self.r.choice(V.EMAIL_DOMAINS)

    def variant(self) -> str:
        n = self.r.randint(100, 9999)
        return self.r.choice([f"c.{n}A>G", f"c.{n}del", f"c.{n}dup", f"p.Arg{self.r.randint(10, 999)}Ter"])

    def clin(self) -> str:
        r = self.r
        k = r.choice(["variant", "result", "age", "dx"])
        if k == "variant":
            return self.variant()
        if k == "result":
            return r.choice(V.RESULT_WORDS)
        if k == "age":
            return r.choice([f"{r.randint(2, 90)}-year-old", f"aged {r.randint(2, 90)}", f"{r.randint(2, 90)}yo"])
        return r.choice(V.DIAGNOSES)


# -- message assembly ------------------------------------------------------
def _addr(name: str, r: random.Random) -> str:
    f, l = name.lower().split()
    return f"{f}.{l}" + "@" + r.choice(V.TRUST_DOMAINS)


def build_email(g: Gen, i: int) -> dict:
    r = g.r
    everyone = V.STAFF_DIR + V.STAFF_NON_DIR
    sender = r.choice(everyone)
    to = r.choice([p for p in V.STAFF_DIR if p != sender])
    cc = r.sample([p for p in everyone if p not in (sender, to)], r.randint(0, 2))
    header_names = {sender, to, *cc}
    people = [[p, _addr(p, r)] for p in (sender, to, *cc)]
    b = Builder()
    b.add("Hi ").plant("staff_name_directory", to.split()[0]).add(",\n\n")
    kinds = r.sample(["ref", "pt", "nhs", "dob", "phone", "addr", "mrn", "ext", "clin", "gmc",
                      "ws", "benign", "config", "nonstaff", "url", "pemail", "stafftwice"], r.randint(2, 5))
    meta = {"has_ref": False, "has_clin": False}
    for k in kinds:
        _sentence(g, b, k, meta, header_names)
        b.add("\n")
    b.add("\nThanks,\n")
    if r.random() < 0.5:
        b.plant("staff_name_directory" if sender in V.STAFF_DIR else "staff_name_header", sender).add("\n")
        if r.random() < 0.5:
            b.plant("phone", g.phone()).add("\n")
    if r.random() < 0.35:
        _adversarial(g, b, r, to)
    fields = {"body": b}
    atts = []
    if r.random() < 0.3:
        fb = Builder(fname="attachment_0")
        fb.add("Report_").plant("attachment_filename", g.pt_name()[1]).add("_")
        fb.plant("attachment_filename", g.referral()).add(".pdf")
        fields["attachment_0"] = fb
        atts.append("pdf")
        meta["has_ref"] = True
    labels = [l for bb in fields.values() for l in bb.labels]
    meta["conflict"] = meta["has_ref"] and meta["has_clin"]
    return {"id": f"msg-{i:05d}", "source": "email", "fields": {k: v.text for k, v in fields.items()},
            "labels": labels, "meta": {**meta, "attachments": atts, "header_people": people}}


def _sentence(g: Gen, b: Builder, k: str, meta: dict, hdr: set | None = None) -> None:
    r = g.r
    hdr = hdr or set()
    if k == "ref":
        b.add("Can you check ").plant("referral_no", g.referral()).add(" and sample ").plant("sample_no", g.sample())
        b.add(" against worksheet ").plant("worksheet_no", g.worksheet()).add("?")
        meta["has_ref"] = True
    elif k == "ws":
        b.add("QC ").plant("qc_no", g.qc()).add(" failed; case ").plant("case_no", g.case()).add(" is on hold.")
    elif k == "pt":
        f, l, full = g.pt_name()
        b.add("The patient is ").plant("patient_name", full).add(", please confirm in Epic.")
    elif k == "nhs":
        b.add("NHS number ").plant("nhs_number", g.nhs_formatted()).add(" appears twice.")
    elif k == "dob":
        b.add("DOB ").plant("dob", g.dob()).add(" does not match the request form.")
    elif k == "phone":
        b.add("Ring me on ").plant("phone", g.phone() if r.random() < 0.7 else g.phone_bracket()).add(" if urgent.")
    elif k == "addr":
        b.add("Letter returned from ").plant("address", g.address()).add(", ").plant("postcode", g.postcode()).add(".")
    elif k == "mrn":
        v, kind = g.mrn()
        b.add("MRN ").plant("mrn", v).add(" is missing from the record.")
    elif k == "ext":
        n = g.ext_name()
        b.add("Supplier contact ").plant("external_contact_name", n).add(" (").plant("external_contact_email", g.ext_email(n)).add(") will call back.")
    elif k == "clin":
        b.add("Result for the case: ").plant("clinical_patient_level", g.clin()).add(", ").plant("clinical_patient_level", g.clin()).add(".")
        meta["has_clin"] = True
    elif k == "gmc":
        b.add("Referring GMC no. ").plant("gmc_id", g.gmc()).add(" needs adding.")
    elif k == "config":
        b.add("The ").plant("config_vocab", r.choice(V.CONFIG_VOCAB)).add(" config change goes live Monday.")
    elif k == "nonstaff":
        who = r.choice(V.STAFF_NON_DIR)
        b.add("Please ask ").plant("staff_name_header" if who in hdr else "staff_name_nondirectory", who).add(" to look at it.")
    elif k == "stafftwice":
        who = r.choice(V.STAFF_DIR)
        b.plant("staff_name_directory", who).add(" will chase it. ").plant("staff_name_directory", who.split()[0]).add(" to confirm.")
    elif k == "pemail":
        _, _, full = g.pt_name()
        b.add("The family wrote from ").plant("patient_email", g.ext_email(full)).add(" yesterday.")
    elif k == "url":
        a, c = r.choice(V.STAFF_NON_DIR).lower().split()
        b.add("Notes are at https://mftexample-my.sharepoint.example.com/personal/").plant("url_personal", f"{a}_{c}_mft_nhs_uk").add("/Documents/notes.docx")
    else:
        b.add(r.choice(V.BENIGN))


def _adversarial(g: Gen, b: Builder, r: random.Random, to: str = "Callum Ridgeway") -> None:
    """Append adversarial variants as a trailing block."""
    b.add("\n--\n")
    kind = r.choice(["odd_group", "words", "newline", "near_miss", "lower_name", "initials",
                     "nickname", "dob_nodyear", "bracket", "mrn_sep", "fullwidth", "name_collision", "ambiguous_surname"])
    d = g.nhs()
    if kind == "odd_group":
        b.add("ref NHS ").plant("nhs_number", f"{d[:2]} {d[2:7]} {d[7:]}", kind)
    elif kind == "words":
        b.add("number is ").plant("nhs_number", " ".join(DIGIT_WORDS[int(c)] for c in d), kind)
    elif kind == "newline":
        b.add("NHS ").plant("nhs_number", f"{d[:3]} {d[3:6]}\n{d[6:]}", kind)
    elif kind == "near_miss":
        bad = d[:9] + str((int(d[9]) + 1) % 10)
        b.add("typo'd NHS ").plant("nhs_number", f"{bad[:3]} {bad[3:6]} {bad[6:]}", kind)
    elif kind == "lower_name":
        f, l, full = g.pt_name()
        b.add("seen pt ").plant("patient_name", full.lower(), kind)
    elif kind == "initials":
        f, l, _ = g.pt_name()
        b.add("patient ").plant("patient_name", f"{f[0]}.{l[0]}.", kind).add(" again")
    elif kind == "nickname":
        f = r.choice(list(V.NICKNAMES))
        b.add("mum says ").plant("patient_name", V.NICKNAMES[f], kind).add(" is unwell")
    elif kind == "dob_nodyear":
        months = ["March", "June", "November"]
        b.add("born ").plant("dob", f"{r.randint(1, 28)}th {r.choice(months)}", kind)
    elif kind == "bracket":
        b.add("tel ").plant("phone", g.phone_bracket(), kind)
    elif kind == "mrn_sep":
        v, _ = g.mrn()
        v = "XQA" + "-" + "".join(str(r.randint(0, 9)) for _ in range(7))
        b.add("hosp no ").plant("mrn", v, kind)
    elif kind == "name_collision":
        b.add("mum says ").plant("patient_name", to.split()[0], kind).add(" is unwell")
    elif kind == "ambiguous_surname":
        b.add("Hi ").plant("staff_name_ambiguous", "Ridgeway", kind).add(",")
    else:  # fullwidth digits
        fw = "".join(chr(ord("0") + int(c) + 0xFF10 - ord("0")) for c in d)
        b.add("NHS ").plant("nhs_number", fw, kind)
    b.add("\n")


UNIQUE_SURNAMES = ["Fairweather", "Lindqvist", "Maddox", "Holloway", "Tallis"]


def build_teams(g: Gen, i: int) -> dict:
    r = g.r
    b = Builder()
    meta = {"has_ref": False, "has_clin": False}
    for k in r.sample(["ref", "pt", "phone", "mrn", "config", "benign", "nonstaff", "pemail"], r.randint(1, 2)):
        _sentence(g, b, k, meta)
        b.add(" ")
    return {"id": f"msg-{i:05d}", "source": "teams", "fields": {"body": b.text}, "labels": b.labels,
            "meta": {**meta, "conflict": meta["has_ref"] and meta["has_clin"]}}


def build_transcript(g: Gen, i: int) -> dict:
    r = g.r
    b = Builder()
    meta = {"has_ref": False, "has_clin": False}
    for _ in range(r.randint(3, 7)):
        x = r.random()
        if x < 0.55:
            b.plant("staff_name_directory", r.choice(V.STAFF_DIR))
        elif x < 0.70:
            b.plant("staff_name_directory", r.choice(UNIQUE_SURNAMES), "bare_speaker")
        elif x < 0.75:
            b.plant("staff_name_ambiguous", "Ridgeway", "ambiguous_surname")
        else:
            b.plant("external_contact_name", g.ext_name())
        b.add(": ")
        _sentence(g, b, r.choice(["ref", "config", "benign", "pt", "nhs", "clin", "benign", "dob"]), meta)
        b.add("\n")
    return {"id": f"msg-{i:05d}", "source": "transcript", "fields": {"body": b.text}, "labels": b.labels,
            "meta": {**meta, "conflict": meta["has_ref"] and meta["has_clin"]}}


def generate(n: int, seed: int) -> list[dict]:
    g = Gen(seed)
    out = []
    for i in range(n):
        x = g.r.random()
        out.append(build_email(g, i) if x < 0.6 else build_teams(g, i) if x < 0.8 else build_transcript(g, i))
    return out


def directory_markdown() -> str:
    """A synthetic People-Directory in the vault's table format (Surname | Full name | Role | Org | Notes)."""
    rows = ["---", "type: reference", "tier: 1", "---", "", "# People Directory", "",
            "| Surname | Full name | Role | Organisation | Notes |", "|---|---|---|---|---|"]
    for full in V.STAFF_DIR + V.DIRECTORY_EXTRA:
        note = "two with this surname" if full.endswith("Ridgeway") else ""
        rows.append(f"| {full.split()[-1]} | {full} | Synthetic role | NWGMS | {note} |")
    return "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "corpus.jsonl")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    msgs = generate(a.n, a.seed)
    with a.out.open("w", encoding="utf-8") as fh:
        for m in msgs:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    (a.out.parent / "People-Directory.md").write_text(directory_markdown(), encoding="utf-8")
    print(f"wrote {len(msgs)} messages, {sum(len(m['labels']) for m in msgs)} labels -> {a.out}")


if __name__ == "__main__":
    main()
