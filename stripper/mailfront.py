"""Email front end (slice 4, ADR 0006): raw-drop contract check, normaliser and the common message record.

Standard library only. Nothing here detects or strips identifiers: that is the core's job. The front end only
turns one raw-drop JSON object (written by the Power Automate flow, one file per message) into the fields the
core consumes, and drops whole regions (quoted history, signature, disclaimers, banners) without parsing them.

    rec = build(raw_dict)          # raises ContractError on a schema problem
    rec.fields                     # {"header": ..., "message": subject + "\\n\\n" + body, "attachment_1": ...}
    rec.header_people              # [(display, address)] for From, To, Cc
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transform as T  # noqa: E402

SCHEMA = "nwgms-rawdrop/1"
REQUIRED = {"schema", "internet_message_id", "conversation_id", "sent", "direction", "from", "to", "subject",
            "body", "attachments"}
OPTIONAL = {"cc", "reply_to", "unique_body"}
SIGNOFF_KEEP = 2          # sign-off line plus one name line
SIGNOFF_WINDOW = 12       # sign-off search covers the last N non-empty lines only


class ContractError(ValueError):
    """The raw-drop file does not match the contract. The file is left in the drop and only counted."""


# ---- contract -----------------------------------------------------------------------------------------
def _person(p, where: str) -> dict:
    if not isinstance(p, dict) or set(p) != {"name", "address"} or not all(isinstance(v, str) for v in p.values()):
        raise ContractError(f"{where}: expected {{name, address}} strings")
    if "@" not in p["address"]:
        raise ContractError(f"{where}: address has no @")
    return p


def _body(b, where: str) -> dict:
    if not isinstance(b, dict) or set(b) != {"content_type", "content"} or b["content_type"] not in ("html", "text") \
            or not isinstance(b["content"], str):
        raise ContractError(f"{where}: expected {{content_type: html|text, content}}")
    return b


def validate(raw) -> dict:
    """Check one raw-drop object against nwgms-rawdrop/1. Error messages name keys only, never values."""
    if not isinstance(raw, dict):
        raise ContractError("top level is not an object")
    keys = set(raw)
    if keys - REQUIRED - OPTIONAL:
        raise ContractError("unknown key(s)")
    if REQUIRED - keys:
        raise ContractError("missing key(s)")
    if raw["schema"] != SCHEMA:
        raise ContractError("schema version")
    for k in ("internet_message_id", "conversation_id", "subject", "sent"):
        if not isinstance(raw[k], str):
            raise ContractError(f"{k}: not a string")
    if not raw["conversation_id"].strip():
        raise ContractError("conversation_id: empty")
    if raw["direction"] not in ("sent", "received"):
        raise ContractError("direction")
    try:
        datetime.fromisoformat(raw["sent"].replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("sent: not ISO 8601") from None
    _person(raw["from"], "from")
    for k in ("to", "cc", "reply_to"):
        if not isinstance(raw.get(k, []), list):
            raise ContractError(f"{k}: not a list")
        for p in raw.get(k, []):
            _person(p, k)
    _body(raw["body"], "body")
    if raw.get("unique_body") is not None:
        _body(raw["unique_body"], "unique_body")
    if not isinstance(raw["attachments"], list):
        raise ContractError("attachments: not a list")
    for a in raw["attachments"]:
        if not isinstance(a, dict) or set(a) != {"name", "content_type", "size", "is_inline"} \
                or not isinstance(a["name"], str) or not isinstance(a["content_type"], str) \
                or not isinstance(a["size"], int) or not isinstance(a["is_inline"], bool):
            raise ContractError("attachments: expected {name, content_type, size, is_inline}")
    return raw


# ---- HTML to text -----------------------------------------------------------------------------------------
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "hr", "pre", "section"}
LINE = {"tr", "li", "br"}     # one line break; every other block tag is a paragraph break
SKIP = {"style", "script", "head", "title", "xml"}
CUT_IDS = ("divrplyfwdmsg", "appendonsend", "mail-editor-reference-message-container")
CUT = "\x00CUT\x00"


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.stopped = False
        self.images = 0
        self.flags: Counter = Counter()
        self.cell = False

    def _stop(self, flag: str) -> None:
        if not self.stopped:
            self.out.append("\n" + CUT + "\n")
            self.flags[flag] += 1
            self.stopped = True

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        ident = (a.get("id", "") + " " + a.get("class", "")).lower()
        if tag == "img":
            self.images += 1
            return
        if self.stopped:
            return
        if tag in SKIP:
            self.skip += 1
            return
        if tag == "blockquote" or any(c in ident for c in CUT_IDS):
            self._stop("quoted_history")
            return
        if "signature" in ident:
            self._stop("signature")
            return
        if tag in ("td", "th"):
            if self.cell:
                self.out.append(" | ")
            self.cell = True
        elif tag in LINE:
            self.out.append("\n")
            self.cell = False
        elif tag in BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag in BLOCK and tag not in LINE and not self.stopped:
            self.out.append("\n\n")
            self.cell = False

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.skip and not self.stopped:
            self.out.append(re.sub(r"[ \t\r\n]+", " ", data))


def html_to_text(html: str) -> tuple[str, int, Counter]:
    p = _Html()
    p.feed(html)
    p.close()
    return "".join(p.out), p.images, p.flags


# ---- text clean-up --------------------------------------------------------------------------------------------
_ZW = re.compile("[​‌‍⁠﻿­]")
_MOJI = re.compile("Ã.|â€|Â[ -¿ ]")


def clean_chars(text: str, flags: Counter) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZW.sub("", text)
    if _MOJI.search(text):
        try:
            fixed = text.encode("cp1252").decode("utf-8")
            if len(_MOJI.findall(fixed)) < len(_MOJI.findall(text)):
                text = fixed
                flags["mojibake_repaired"] += 1
        except (UnicodeEncodeError, UnicodeDecodeError):
            flags["mojibake_left"] += 1
    return unicodedata.normalize("NFKC", text)


_HIST = [re.compile(p, re.I) for p in (
    r"^-{2,}\s*original message\s*-{2,}\s*$",
    r"^_{10,}\s*$",
    r"^on .{1,200} wrote:\s*$",
    r"^>",
)]
_FWD = [re.compile(p, re.I) for p in (r"^-{2,}\s*forwarded message\s*-{2,}\s*$", r"^begin forwarded message:?\s*$")]
_FROM = re.compile(r"^\s*from:\s", re.I)
_SENT = re.compile(r"^\s*(sent|date):\s", re.I)


def cut_history(lines: list[str], flags: Counter) -> list[str]:
    """Drop everything from the first quoted-history or forward marker. Never parses what follows."""
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == CUT:
            return lines[:i]
        if any(r.match(s) for r in _FWD):
            flags["forwarded_dropped"] += 1
            return lines[:i]
        if any(r.match(s) for r in _HIST) or (_FROM.match(s) and any(_SENT.match(x) for x in lines[i + 1:i + 5])):
            flags["quoted_history"] += 1
            return lines[:i]
    return lines


DISCLAIMER_MARKERS = (
    "originated from outside", "caution: external", "external email", "do not click links or open attachments",
    "this message may contain confidential", "this e-mail and any attachments", "this email and any attachments",
    "if you are not the intended recipient", "disclaimer", "nhsmail is the secure email", "virus checked",
)


def drop_disclaimers(text: str, flags: Counter) -> str:
    paras = re.split(r"\n\s*\n", text)
    keep = []
    for p in paras:
        if any(m in p.lower() for m in DISCLAIMER_MARKERS):
            flags["disclaimer"] += 1
        else:
            keep.append(p)
    return "\n\n".join(keep)


_SIGNOFF = re.compile(r"^(many thanks|thanks( again| very much)?|thank you|kind regards|best regards|warm regards|"
                      r"regards|best wishes|best|cheers|with thanks|all the best|speak soon)[,.!]?$", re.I)


def cut_signature(lines: list[str], flags: Counter) -> list[str]:
    for i, ln in enumerate(lines):
        if ln.rstrip() == "--" or ln == "-- " or ln.strip().lower().startswith("sent from my"):
            flags["signature"] += 1
            return lines[:i]
    nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
    for i in nonempty[-SIGNOFF_WINDOW:]:
        if _SIGNOFF.match(lines[i].strip()):
            after = [j for j in nonempty if j > i]
            keep = after[:SIGNOFF_KEEP - 1]
            if len(after) > len(keep):
                flags["signature"] += 1
            return lines[:(keep[-1] if keep else i) + 1]
    return lines


def normalise_body(body: dict, flags: Counter) -> tuple[str, int]:
    """One body (HTML or text) to clean plain text. Returns (text, inline image count)."""
    images = 0
    text = body["content"]
    if body["content_type"] == "html":
        text, images, f = html_to_text(text)
        flags.update(f)
    text = clean_chars(text, flags)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    lines = cut_history(lines, flags)
    text = drop_disclaimers("\n".join(lines), flags)
    lines = cut_signature(text.split("\n"), flags)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, images


_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.I)


def normalise_subject(subject: str, flags: Counter) -> str:
    s = re.sub(r"\s+", " ", clean_chars(subject, flags)).strip()
    while True:
        m = _PREFIX.match(s)
        if not m:
            return s
        if m.group(1).lower() != "re":
            flags["forwarded_subject"] += 1
        s = s[m.end():]


def display_name(name: str) -> str:
    """'Surname, Forename (Org)' -> 'Forename Surname'. Quotes and parentheticals dropped."""
    n = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name).replace('"', " ")
    n = re.sub(r"\s+", " ", n).strip()
    if n.count(",") == 1:
        a, b = (x.strip() for x in n.split(","))
        n = f"{b} {a}".strip()
    return n


# ---- the common message record -----------------------------------------------------------------------------
@dataclass
class MessageRecord:
    fields: dict
    header_people: list
    conversation_id: str          # used only to derive the thread key; never written out
    sent: str
    direction: str
    to_n: int
    cc_n: int
    reply_to: str
    inline_images: int
    flags: Counter = field(default_factory=Counter)
    sent_exact: str = ""          # UTC to the second; part of the dedup basis only


def _render(p: dict) -> str:
    """Trust-domain participants by name only; external ones by address, which the core turns into CONTACT-nnnn."""
    addr = p["address"].strip()
    if T.Transformer._trusted(addr):
        return display_name(p["name"]) or addr
    return addr


def build(raw: dict) -> MessageRecord:
    raw = validate(raw)
    flags: Counter = Counter()
    use = raw.get("unique_body") or raw["body"]
    if raw.get("unique_body"):
        flags["unique_body"] += 1
    body, img_tags = normalise_body(use, flags)
    subject = normalise_subject(raw["subject"], flags)
    cc = raw.get("cc", [])
    header = "\n".join([
        "From: " + _render(raw["from"]),
        "To: " + (", ".join(_render(p) for p in raw["to"]) or "none"),
        "Cc: " + (", ".join(_render(p) for p in cc) or "none"),
    ])
    fields = {"header": header, "message": subject + "\n\n" + body}
    inline = 0
    n = 0
    for a in raw["attachments"]:
        if a["is_inline"]:
            inline += 1
            continue
        n += 1
        fields[f"attachment_{n}"] = a["name"]
    people = [(display_name(p["name"]), p["address"].strip()) for p in [raw["from"], *raw["to"], *cc]]
    rt = raw.get("reply_to", [])
    sender = raw["from"]["address"].strip().lower()
    if not rt:
        reply = "none"
    elif all(p["address"].strip().lower() == sender for p in rt):
        reply = "same as sender"
    else:
        reply = f"differs ({len(rt)})"
    utc = datetime.fromisoformat(raw["sent"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return MessageRecord(fields, people, raw["conversation_id"], utc.strftime("%Y-%m-%d %H:%MZ"), raw["direction"],
                         len(raw["to"]), len(cc), reply, max(inline, img_tags), flags, utc.strftime("%Y-%m-%dT%H:%M:%SZ"))


def dedup_basis(rec: MessageRecord, content_hash: str) -> str:
    """Message hash basis (ADR 0006): normalised content plus sent time plus conversation ID, so the same message
    seen in two folders collapses, but two different short replies with the same words do not."""
    return "\x1d".join([content_hash, rec.sent_exact, rec.conversation_id.strip()])


def split_check(stripped: dict) -> Counter:
    """Shape check run on the stripped fields before release (fail closed): header has its three lines and the
    message still splits into subject and body at the first blank line."""
    bad = Counter()
    h = stripped.get("header", "").split("\n")
    if len(h) != 3 or not (h[0].startswith("From: ") and h[1].startswith("To: ") and h[2].startswith("Cc: ")):
        bad["FRONT_SHAPE"] += 1
    if "\n\n" not in stripped.get("message", ""):
        bad["FRONT_SHAPE"] += 1
    return bad
