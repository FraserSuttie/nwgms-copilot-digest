"""Synthetic raw-drop generator (slice 4). Invented from scratch; never derived from a real message.

Wraps the labelled synthetic emails from generate.py in nwgms-rawdrop/1 JSON (the shape the Power Automate flow
will write) and adds the things real mail carries: HTML bodies, tables, Outlook signature blocks, disclaimers,
external-sender banners, quoted and forwarded history (with their own planted identifiers, which must never reach
the output), uniqueBody, inline images, mojibake, nbsp and zero-width characters, shared conversation IDs and
duplicate files.

    python stripper/corpus/rawdrop.py --dir D [--n 1200] [--seed 20261027]   # writes D/drop, labels.jsonl, directory.md, vocab.txt
    python stripper/corpus/rawdrop.py --dir D --score OUT                      # counts only: planted values in OUT

D must be outside the repo (the runner refuses a drop inside it). Output is synthetic and disposable.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import generate as C  # noqa: E402
import transform as T  # noqa: E402
import vocab as V  # noqa: E402

SUBJECTS = ["Query about the panel update", "Worksheet question", "Update on the sample queue",
            "Access request for the test system", "Follow up on yesterday", "Panel configuration change",
            "Quick question about the report", "Change request progress"]
BANNER = "CAUTION: This email originated from outside the organisation. Do not click links or open attachments unless you recognise the sender."
DISCLAIMER = ("This message may contain confidential information. If you are not the intended recipient please "
              "inform the sender and delete it. Switchboard {phone}.")
START = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
NAME_CATS = {"patient_name", "external_contact_name", "staff_name_nondirectory", "staff_name_ambiguous"}


def _p(text: str) -> str:
    return "".join(f"<p>{'<br>'.join(html.escape(l) for l in para.split(chr(10)))}</p>"
                   for para in text.split("\n\n") if para.strip())


def _person(name: str, addr: str, r: random.Random) -> dict:
    if r.random() < 0.3:
        f, _, l = name.partition(" ")
        name = f"{l}, {f} (MFT)"
    return {"name": name, "address": addr}


def build(g: C.Gen, i: int, conv: int, first: bool) -> tuple[dict, dict]:
    r = g.r
    m = C.build_email(g, i)
    body = m["fields"]["body"]
    extra: dict = {"history": []}
    hp = m["meta"]["header_people"]
    frm, to, cc = hp[0], [hp[1]], hp[2:]
    rawcc = [_person(n, a, r) for n, a in cc]
    if r.random() < 0.1:
        en = g.ext_name()
        rawcc.append({"name": en, "address": g.ext_email(en)})
        extra["ext_header"] = en
    is_html = r.random() < 0.8
    prefix = "" if first else r.choice(["RE: ", "RE: ", "FW: "])
    subject = prefix + r.choice(SUBJECTS)
    new = body
    if r.random() < 0.1:
        new = new.replace("\n\nThanks,", "\n\nHere’s the update.\n\nThanks,", 1).encode("utf-8").decode("cp1252", "replace")
        extra["mojibake"] = True
    if r.random() < 0.1:
        new = new.replace(" ", " ", 2).replace("Thanks", "Tha​nks", 1)
    tail_txt, tail_html, head_txt, head_html = "", "", "", ""
    if r.random() < 0.25:
        head_txt, head_html = BANNER + "\n\n", f"<p><b>{html.escape(BANNER)}</b></p>"
    if is_html and r.random() < 0.3:
        tail_html += (f'<div id="Signature"><p>{html.escape(frm[0])}<br>Synthetic Role<br>Genomics Laboratory<br>'
                      f'{g.phone()}</p></div>')
    if r.random() < 0.15:
        tail_html += "<table><tr><th>Test</th><th>Status</th></tr><tr><td>panel update</td><td>done</td></tr></table>"
        tail_txt += "\n\nTest | Status\npanel update | done"
    if r.random() < 0.4:
        ph = g.phone()
        tail_txt += "\n\n" + DISCLAIMER.format(phone=ph)
        tail_html += _p(DISCLAIMER.format(phone=ph))
    hist_txt = hist_html = ""
    kind = r.random()
    if not first and kind < 0.7:
        f, l, full = g.pt_name()
        nhs = g.nhs()
        extra["history"] += [full, nhs]
        old = f"Hi,\n\nPlease see {full} with NHS number {nhs} attached.\n\nThanks"
        style = r.choice(["outlook", "original", "wrote", "forward"])
        if style == "forward":
            hist_txt = f"\n\n---------- Forwarded message ---------\nFrom: {frm[0]}\nDate: 1 Oct 2026\n\n{old}"
            hist_html = _p(hist_txt)
        elif style == "outlook":
            hist_html = (f'<div id="divRplyFwdMsg"><b>From:</b> {html.escape(frm[0])}<br><b>Sent:</b> 1 October 2026'
                         f"</div><div>{_p(old)}</div>")
            hist_txt = f"\n\n________________________________\nFrom: {frm[0]}\nSent: 1 October 2026\n\n{old}"
        elif style == "original":
            hist_txt = f"\n\n-----Original Message-----\nFrom: {frm[0]}\nSent: 1 October 2026\n\n{old}"
            hist_html = _p(hist_txt)
        else:
            hist_txt = f"\n\nOn Wed, 1 Oct 2026, {frm[0]} wrote:\n" + "\n".join("> " + x for x in old.split("\n"))
            hist_html = f"<p>On Wed, 1 Oct 2026, {html.escape(frm[0])} wrote:</p><blockquote>{_p(old)}</blockquote>"
    atts = []
    if "attachment_0" in m["fields"]:
        atts.append({"name": m["fields"]["attachment_0"], "content_type": "application/pdf",
                     "size": r.randint(1000, 900000), "is_inline": False})
    img = ""
    if is_html and r.random() < 0.15:
        img = '<img src="cid:image001.png@01DB0000">'
        atts.append({"name": "image001.png", "content_type": "image/png", "size": r.randint(500, 9000), "is_inline": True})
        extra["inline"] = True
    if is_html:
        content = f"<html><head><style>p {{margin:0}}</style></head><body>{head_html}{_p(new)}{img}{tail_html}{hist_html}</body></html>"
        uniq = f"<html><body>{head_html}{_p(new)}{img}{tail_html}</body></html>"
    else:
        content = head_txt + new + tail_txt + hist_txt
        uniq = head_txt + new + tail_txt
    raw = {"schema": "nwgms-rawdrop/1", "internet_message_id": f"<synthetic-{i:05d}@example.invalid>",
           "conversation_id": f"synthetic-conversation-{conv:05d}",
           "sent": (START + timedelta(minutes=7 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "direction": "sent" if r.random() < 0.2 else "received",
           "from": _person(*frm, r), "to": [_person(n, a, r) for n, a in to], "cc": rawcc,
           "subject": subject, "body": {"content_type": "html" if is_html else "text", "content": content},
           "attachments": atts}
    if r.random() < 0.1:
        raw["reply_to"] = [{"name": raw["from"]["name"], "address": raw["from"]["address"]}]
    if hist_txt and r.random() < 0.5:
        raw["unique_body"] = {"content_type": "html" if is_html else "text", "content": uniq}
    return raw, {"id": m["id"], "fields": m["fields"], "labels": m["labels"], "extra": extra}


def generate(n: int, seed: int) -> list[tuple[dict, dict]]:
    g = C.Gen(seed)
    out, conv, left = [], 0, 0
    for i in range(n):
        first = left == 0
        if first:
            conv += 1
            left = g.r.randint(1, 4)
        left -= 1
        out.append(build(g, i, conv, first))
    return out


def write(d: Path, n: int, seed: int) -> dict:
    drop = d / "drop"
    drop.mkdir(parents=True, exist_ok=True)
    r = random.Random(seed + 1)
    dups = 0
    with open(d / "labels.jsonl", "w", encoding="utf-8") as lf:
        for raw, lab in generate(n, seed):
            data = json.dumps(raw, ensure_ascii=False)
            (drop / f"{lab['id']}.json").write_text(data, encoding="utf-8")
            if r.random() < 0.03:
                (drop / f"{lab['id']}-dup.json").write_text(data, encoding="utf-8")
                dups += 1
            lf.write(json.dumps(lab) + "\n")
    (d / "directory.md").write_text(C.directory_markdown(), encoding="utf-8")
    import pipeline as P  # the same stand-in vocabulary the Run 3 corpus report uses
    (d / "vocab.txt").write_text("\n".join(sorted(P.corpus_vocab())) + "\n", encoding="utf-8")
    return {"messages": n, "duplicate_files": dups}


def score(d: Path, out: Path) -> dict:
    """Per released block (matched to its synthetic message by sent time, unique per message): planted values still
    present, scored like the Run 3 pipeline report. Counts only."""
    blocks = {}
    for p in (out / "Comms" / "_Messages").glob("THR-*.md"):
        for b in re.findall(r"<!-- block:\w+ -->(.*?)<!-- /block:", p.read_text(encoding="utf-8"), re.S):
            m = re.search(r"- sent: (\S+ \S+)", b)
            blocks[m.group(1)] = unicodedata.normalize("NFKC", b)
    staff = {t for full in V.STAFF_DIR + V.DIRECTORY_EXTRA + V.STAFF_NON_DIR for t in full.split()}
    leaks, names, hist, n_name_msgs = Counter(), Counter(), 0, 0
    for i, line in enumerate((d / "labels.jsonl").read_text(encoding="utf-8").splitlines()):
        lab = json.loads(line)
        text = blocks.get((START + timedelta(minutes=7 * i)).strftime("%Y-%m-%d %H:%MZ"))
        if text is None:
            continue
        hit = False
        for l in lab["labels"]:
            if l["field"].startswith("attachment_"):
                continue
            v = unicodedata.normalize("NFKC", lab["fields"][l["field"]][l["start"]:l["end"]])
            if l["category"] in T.LEAK_CATS and l["action"] == "strip" and v.strip() and v in text:
                leaks[l["category"]] += 1
            if l["category"] in NAME_CATS:
                toks = [t for t in re.findall(r"[A-Za-z]{3,}", v) if t not in staff]
                if any(re.search(rf"\b{re.escape(t)}\b", text) for t in toks):
                    names[l["kind"]] += 1
                    hit = True
        n_name_msgs += hit
        hist += sum(1 for v in lab["extra"]["history"] if v in text)
        if lab["extra"].get("ext_header") and lab["extra"]["ext_header"] in text:
            leaks["external_header_name"] += 1
    return {"released blocks": len(blocks), "planted strip values present": dict(leaks),
            "released blocks with a planted name fragment": n_name_msgs,
            "name fragments by kind": dict(names), "history plants present": hist}


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic raw drop for the email runner (counts only)")
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--score", type=Path)
    a = ap.parse_args()
    if a.score:
        for k, v in score(a.dir, a.score).items():
            print(f"{k}: {v or 'none'}")
    else:
        print(write(a.dir, a.n, a.seed))


if __name__ == "__main__":
    main()
