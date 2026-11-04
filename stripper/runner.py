"""Email batch runner (slice 4, ADR 0006). Run by Fraser on his laptop only; prints COUNTS ONLY, never values.

    python stripper/runner.py --drop D --state S --out O --directory P [--vocab F] [--ignore F]
                              [--retry-quarantined] [--lane-active] [--caps-limit N]

--drop       raw-drop folder (one nwgms-rawdrop/1 JSON per message). May be OneDrive; refused in the repo or a vault.
--state      local state (key store, review state, quarantine, seen index). Refused in the repo or OneDrive.
--out        output root: writes <out>/Comms/_Messages/THR-*.md (append-only) and <out>/_Intake/comms-batch-*.md.
             Refused in the repo; refused inside the vault while the lane is inactive (unless --lane-active).
--directory  People-Directory markdown (read only).
--vocab      config vocabulary file, one term per line (load_wordlist). Refused in the repo or OneDrive.
--ignore     review-queue ignore list, one term per line. Refused in the repo or OneDrive.
--caps-limit unknown capitalised words that quarantine a message (DENS_CAPS), default 1: one in any field. Above 1 it is
             the message total (ADR 0010). The report prints the limit used.

NER is always required here: the run stops if the layer is not active. Drop files are never modified or deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keystore as K  # noqa: E402
import mailfront as M  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import pipeline as P  # noqa: E402
import quarantine as Q  # noqa: E402
import residual as R  # noqa: E402
import transform as T  # noqa: E402

VERSION = T.STRIPPER_VERSION
SEEN = "seen.jsonl"
VAULT_MARKER = "## 8. Comms lane"


class Refused(ValueError):
    """A path or setting the runner will not accept. The message never contains content."""


# ---- path rules ----------------------------------------------------------------------------------------------
def _resolved(p) -> Path:
    return Path(p).expanduser().resolve()


def _in_repo(p: Path) -> bool:
    return p == G.REPO_ROOT or G.REPO_ROOT in p.parents


def _in_onedrive(p: Path) -> bool:
    return "onedrive" in str(p).lower()


def _vault_root(p: Path) -> Path | None:
    for d in (p, *p.parents):
        f = d / "CLAUDE.md"
        try:
            if f.is_file() and VAULT_MARKER in f.read_text(encoding="utf-8", errors="ignore"):
                return d
        except OSError:
            continue
    return None


def check_paths(drop, state, out, vocab=None, ignore=None, lane_active=False) -> tuple[Path, Path, Path]:
    drop, state, out = _resolved(drop), _resolved(state), _resolved(out)
    if _in_repo(drop):
        raise Refused("drop must not be inside the repo")
    if _vault_root(drop):
        raise Refused("drop must not be inside the vault (Z2 is never reachable by Claude)")
    if _in_repo(state) or _in_onedrive(state):
        raise Refused("state must be local: not inside the repo or OneDrive")
    if _in_repo(out):
        raise Refused("out must not be inside the repo")
    if _vault_root(out) and not lane_active:
        raise Refused("out is inside the vault and the Comms lane is inactive (CLAUDE.md section 8); pass --lane-active only once a test report passes")
    for a, b in ((drop, out), (drop, state), (state, out)):
        if a == b or a in b.parents or b in a.parents:
            raise Refused("drop, state and out must be separate folders")
    for f in (vocab, ignore):
        if f is not None:
            fp = _resolved(f)
            if _in_repo(fp) or _in_onedrive(fp):
                raise Refused("vocabulary and ignore files must be local: not inside the repo or OneDrive")
    return drop, state, out


# ---- seen index (Z3, keyed hashes and decisions only) -----------------------------------------------------------
class Seen:
    def __init__(self, state: Path) -> None:
        self.path = state / SEEN
        self.d: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.d[rec["msg"]] = rec["decision"]

    def add(self, msg_hash: str, decision: str) -> None:
        self.d[msg_hash] = decision
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"msg": msg_hash, "decision": decision,
                                 "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")


# ---- output (append-only per thread) ----------------------------------------------------------------------------
def _append(path: Path, text: str, tries: int = 6) -> None:
    """One write per block; retries transient Windows/OneDrive locks."""
    for n in range(tries):
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            return
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(0.2 * (n + 1))


def thread_header(thread: str, today: str) -> str:
    return ("---\n"
            f"thread: {thread}\n"
            f"created: {today}\n"
            f"written-by: nwgms-stripper {VERSION}\n"
            "note: machine-written, append-only, never hand-edited\n"
            "tier: Comms tokens (vault CLAUDE.md sections 3 and 8)\n"
            "---\n\n")


def _counts(c: Counter) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "none"


def render_block(msg_hash: str, rec: M.MessageRecord, stripped: dict, counts: Counter) -> str:
    frm, to, cc = (stripped[k] for k in M.HEADER_FIELDS)
    subject, _, body = stripped["message"].partition("\n\n")
    atts = [stripped[k] for k in sorted((k for k in stripped if k.startswith("attachment_")),
                                        key=lambda k: int(k.split("_")[1]))]
    flags = ", ".join(k.replace("_", " ") for k in sorted(rec.flags)) or "none"
    return (f"<!-- block:{msg_hash} -->\n"
            f"### Message {msg_hash[:12]}\n"
            f"- sent: {rec.sent} | direction: {rec.direction}\n"
            f"- from: {frm} | to: {rec.to_n} ({to}) | cc: {rec.cc_n} ({cc}) | reply-to: {rec.reply_to}\n"
            f"- attachments: {'; '.join(atts) or 'none'} | inline images: {rec.inline_images} (not kept)\n"
            f"- redactions: {_counts(counts)} | flags: {flags}\n"
            f"- stripper: {VERSION}\n\n"
            f"**Subject:** {subject}\n\n"
            f"{body}\n\n"
            f"<!-- /block:{msg_hash} -->\n\n")


def torn_blocks(text: str) -> int:
    starts = text.count("<!-- block:")
    ends = text.count("<!-- /block:")
    return max(0, starts - ends)


# ---- the run -------------------------------------------------------------------------------------------------
@dataclass
class Report:
    files: int = 0
    unreadable: int = 0
    schema_rejects: int = 0
    duplicates: int = 0
    released: int = 0
    quarantined: int = 0
    reasons: Counter = field(default_factory=Counter)
    threads_touched: int = 0
    threads_new: int = 0
    torn_blocks: int = 0
    already_written: int = 0
    attachments: int = 0
    inline_images: int = 0
    flags: Counter = field(default_factory=Counter)
    stopped: str = ""
    caps_limit: int = R.Limits().unknown_caps


def run(drop, state, out, directory_md: str, vocab=frozenset(), ignore_words=frozenset(), retry_quarantined=False,
        lane_active=False, protector=None, use_ner=True, year=None, vocab_path=None, ignore_path=None,
        now: datetime | None = None, limits: R.Limits = R.Limits()) -> Report:
    drop, state, out = check_paths(drop, state, out, vocab_path, ignore_path, lane_active)
    protector = protector or K.default_protector()
    now = now or datetime.now(timezone.utc)
    state.mkdir(parents=True, exist_ok=True)
    store = K.KeyStore(state, protector)
    quar = Q.Quarantine(state, protector)
    gstate = G.State(state, protector)
    people = gstate.people(G.parse_directory(directory_md))
    ignore = gstate.ignore() | {G.norm(w) for w in ignore_words}
    review: Counter = Counter()
    tr = T.Transformer(store, people, ignore=ignore, review=review, use_ner=use_ner, year=year)
    pipe = P.Pipeline(tr, quar, limits=limits, vocab=vocab)
    seen = Seen(state)
    msg_dir = out / "Comms" / "_Messages"
    rep = Report(caps_limit=limits.unknown_caps)
    touched: dict[str, list[str]] = {}
    new_threads: set[str] = set()
    try:
        for f in sorted(drop.glob("*.json")):
            rep.files += 1
            try:
                raw = json.loads(f.read_bytes().decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError, OSError):
                rep.unreadable += 1
                continue
            try:
                M.validate(raw)
            except M.ContractError:
                rep.schema_rejects += 1
                continue
            try:
                rec = M.build(raw)
            except Exception:  # noqa: BLE001 - fail closed: park the raw object, release nothing
                h = store.message_hash(json.dumps(raw, sort_keys=True))
                if seen.d.get(h) and not (retry_quarantined and seen.d[h] == "quarantined"):
                    rep.duplicates += 1
                    continue
                quar.put(h, {"raw": raw}, Counter({"FRONT_ERROR": 1}), VERSION)
                store.audit(h, "quarantined", Counter({"FRONT_ERROR": 1}), VERSION)
                store.save()
                seen.add(h, "quarantined")
                rep.quarantined += 1
                rep.reasons["FRONT_ERROR"] += 1
                continue
            h = store.message_hash(M.dedup_basis(rec, tr.hash_fields(rec.fields)))
            prior = seen.d.get(h)
            if prior == "ok" or (prior == "quarantined" and not retry_quarantined):
                rep.duplicates += 1
                continue
            rep.flags.update(rec.flags)
            rep.inline_images += rec.inline_images
            rep.attachments += sum(1 for k in rec.fields if k.startswith("attachment_"))
            o = pipe.process(rec.fields, rec.header_people, check=M.split_check)
            if o.status != "ok":
                seen.add(h, "quarantined")
                rep.quarantined += 1
                rep.reasons.update(o.reasons)
                continue
            thread = store.thread_key(rec.conversation_id)
            msg_dir.mkdir(parents=True, exist_ok=True)
            path = msg_dir / f"{thread}.md"
            if not path.exists():
                with open(path, "x", encoding="utf-8", newline="\n") as fh:
                    fh.write(thread_header(thread, now.strftime("%Y-%m-%d")))
                new_threads.add(thread)
            store.link_block(h, o.msg_hash)       # ATT originals are keyed by field hash; blocks show h (ADR 0007)
            existing = path.read_text(encoding="utf-8")
            if f"<!-- /block:{h} -->" in existing:
                rep.already_written += 1          # crash between append and seen: do not write twice
            else:
                _append(path, render_block(h, rec, o.fields, o.counts))
                touched.setdefault(thread, []).append(h)
            seen.add(h, "ok")
            rep.released += 1
    except Q.QuarantineError:
        rep.stopped = "quarantine write failed: run stopped, nothing further released"
    finally:
        gstate.merge_counts(review)
        gstate.save()
        store.save()
    rep.threads_touched = len(touched)
    rep.threads_new = len(new_threads)
    if msg_dir.exists():
        rep.torn_blocks = sum(torn_blocks(p.read_text(encoding="utf-8")) for p in msg_dir.glob("THR-*.md"))
    if rep.files:
        write_manifest(out, rep, touched, now)
    return rep


def write_manifest(out: Path, rep: Report, touched: dict, now: datetime) -> Path:
    """Batch manifest for Rule 9 batch mode (ADR 0006, option E2): counts and block hashes only."""
    intake = out / "_Intake"
    intake.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["---", "type: comms-batch", f"stripper: {VERSION}", f"run: {now.strftime('%Y-%m-%dT%H:%MZ')}",
             "redaction-method: stripper", "---", "", f"# Comms batch {stamp}", ""]
    lines += [f"- {k}: {v}" for k, v in report_counts(rep).items()]
    lines += ["", "## Threads touched", ""]
    for t in sorted(touched):
        lines.append(f"- [[{t}]]: {len(touched[t])} new block(s): " + ", ".join(f"`{h[:12]}`" for h in touched[t]))
    if not touched:
        lines.append("- none")
    path = intake / f"comms-batch-{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def report_counts(rep: Report) -> dict:
    return {"files read": rep.files, "unreadable": rep.unreadable, "schema rejects": rep.schema_rejects,
            "duplicates skipped": rep.duplicates, "released": rep.released, "quarantined": rep.quarantined,
            "unknown-caps limit": rep.caps_limit, "quarantine reasons": _counts(rep.reasons), "threads touched": rep.threads_touched,
            "threads new": rep.threads_new, "torn blocks": rep.torn_blocks,
            "already written (replay)": rep.already_written, "attachments listed": rep.attachments,
            "inline images (not kept)": rep.inline_images, "front-end flags": _counts(rep.flags)}


def print_report(rep: Report, out=sys.stdout) -> None:
    print(f"nwgms-stripper {VERSION} (counts only)", file=out)
    for k, v in report_counts(rep).items():
        print(f"{k}: {v}", file=out)
    if rep.stopped:
        print(f"STOPPED: {rep.stopped}", file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drop", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--directory", required=True, type=Path)
    ap.add_argument("--vocab", type=Path)
    ap.add_argument("--ignore", type=Path)
    ap.add_argument("--retry-quarantined", action="store_true")
    ap.add_argument("--lane-active", action="store_true")
    ap.add_argument("--caps-limit", type=int, default=R.Limits().unknown_caps)
    a = ap.parse_args(argv)
    try:
        check_paths(a.drop, a.state, a.out, a.vocab, a.ignore, a.lane_active)
        if a.caps_limit < 1:
            raise Refused("--caps-limit must be 1 or more")
        vocab = R.load_wordlist(a.vocab) if a.vocab else frozenset()
        ignore = R.load_wordlist(a.ignore) if a.ignore else frozenset()
        directory = a.directory.read_text(encoding="utf-8")
        N.load()
        if not str(N.STATUS).startswith("active"):
            raise Refused("the NER layer is not active; real runs require it")
        rep = run(a.drop, a.state, a.out, directory, vocab, ignore, a.retry_quarantined, a.lane_active,
                  use_ner=True, vocab_path=a.vocab, ignore_path=a.ignore,
                  limits=R.Limits(unknown_caps=a.caps_limit))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never print an exception message: it could carry content
        print(f"error: {type(exc).__name__} (message suppressed)", file=sys.stderr)
        return 1
    print_report(rep)
    return 3 if rep.stopped else 0


if __name__ == "__main__":
    sys.exit(main())
