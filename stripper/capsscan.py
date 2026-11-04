"""Read-only scan of a raw drop for the caps tools (ADR 0010). Used by capsdiag.py (counts only) and capsvocab.py.

It runs the production decision path (`Pipeline.evaluate`: transform, residual scan, density, cue check) over every
message in the drop and keeps, IN MEMORY ONLY, the unknown capitalised words with their shape. It never writes: no
quarantine, no seen index, no audit line, no key-store save, no output blocks. The key store must already exist (for
example the state folder of an earlier baseline run); the scan refuses rather than create one. Nothing here prints.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keystore as K  # noqa: E402
import mailfront as M  # noqa: E402
import namegate as G  # noqa: E402
import ner as N  # noqa: E402
import pipeline as P  # noqa: E402
import residual as R  # noqa: E402
import runner as RN  # noqa: E402
import transform as T  # noqa: E402

Refused = RN.Refused
SECTIONS = ("subject", "body", "from", "to", "cc")
_SECTION_OF = {"header_from": "from", "header_to": "to", "header_cc": "cc"}
_PRE = re.compile(r"([A-Za-z][A-Za-z'’-]*)|([.!?;:\n])")
_ABBR = re.compile(r"\b(pt|mr|mrs|ms|mx)\.", re.I)
_LOWER = re.compile(r"\b[a-z]{3,}\b")


@dataclass
class WordHit:
    word: str            # as written; lives in memory only
    key: str             # lower-case
    section: str
    initial: bool
    cue: bool            # within two words after a patient cue (CUES)
    ext: bool            # a word of an external header participant's display name


@dataclass
class Scanned:
    thread: str
    reasons: Counter
    transformed: bool
    profiles: dict               # section -> Counter (R.PROFILE_KEYS)
    field_totals: list           # unknown-cap count per non-attachment field (the production per-field rule)
    hits: list                   # list[WordHit]

    @property
    def total(self) -> int:
        return sum(self.field_totals)

    @property
    def others(self) -> bool:
        """Any reason other than DENS_CAPS."""
        return any(k != "DENS_CAPS" for k in self.reasons)


@dataclass
class ScanResult:
    files: int = 0
    unreadable: int = 0
    schema_rejects: int = 0
    front_errors: int = 0
    duplicates: int = 0
    msgs: list = field(default_factory=list)
    lower_seen: set = field(default_factory=set)      # lower-case words seen in message text (memory only)
    directory_tokens: frozenset = frozenset()
    vocab: frozenset = frozenset()


def _after_cue(t: str, pos: int) -> bool:
    toks = [m.group(0) for m in _PRE.finditer(_ABBR.sub(lambda m: m.group(1), t[max(0, pos - 80):pos]))]
    for seen, tok in enumerate(reversed(toks)):
        if tok in ".!?;:\n":
            return False
        if tok.lower() in R.CUES:
            return True
        if seen >= 1:
            return False
    return False


def _external_words(header_people) -> frozenset:
    words = set()
    for display, addr in header_people:
        dom = addr.rsplit("@", 1)[-1].lower()
        if not any(dom == d or dom.endswith("." + d) for d in G.P.ALLOWED_EMAIL_DOMAINS):
            words.update(re.findall(r"[a-z0-9']+", display.lower()))
    return frozenset(words)


def check_read_paths(drop, state, vocab=None, ignore=None, phrases=None) -> tuple[Path, Path]:
    drop, state = RN._resolved(drop), RN._resolved(state)
    if RN._in_repo(drop):
        raise Refused("drop must not be inside the repo")
    if RN._vault_root(drop):
        raise Refused("drop must not be inside the vault (Z2 is never reachable by Claude)")
    if RN._in_repo(state) or RN._in_onedrive(state):
        raise Refused("state must be local: not inside the repo or OneDrive")
    if state == drop or state in drop.parents or drop in state.parents:
        raise Refused("drop and state must be separate folders")
    for f in (vocab, ignore, phrases):
        if f is not None:
            fp = RN._resolved(f)
            if RN._in_repo(fp) or RN._in_onedrive(fp):
                raise Refused("vocabulary and ignore files must be local: not inside the repo or OneDrive")
    if not (state / K.KeyStore.MASTER).is_file():
        raise Refused("no key store in the state directory: point --state at an existing baseline state (the scan never creates one)")
    return drop, state


def scan_drop(drop, state, directory_md: str, vocab=frozenset(), ignore_words=frozenset(), use_ner: bool = True,
              year=None, protector=None, vocab_path=None, ignore_path=None, phrases_path=None) -> ScanResult:
    drop, state = check_read_paths(drop, state, vocab_path, ignore_path, phrases_path)
    protector = protector or K.default_protector()
    store = K.KeyStore(state, protector)          # master exists (checked above): this only reads
    gstate = G.State(state, protector)
    people = gstate.people(G.parse_directory(directory_md))
    ignore = gstate.ignore() | {G.norm(w) for w in ignore_words}
    tr = T.Transformer(store, people, ignore=ignore, review=Counter(), use_ner=use_ner, year=year)
    pipe = P.Pipeline(tr, None, vocab=vocab)      # no quarantine: evaluate() never writes
    res = ScanResult(directory_tokens=frozenset(people.tokens), vocab=frozenset(w.lower() for w in vocab))
    seen: set = set()
    for f in sorted(drop.glob("*.json")):
        res.files += 1
        try:
            rawobj = json.loads(f.read_bytes().decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, OSError):
            res.unreadable += 1
            continue
        try:
            M.validate(rawobj)
        except M.ContractError:
            res.schema_rejects += 1
            continue
        try:
            rec = M.build(rawobj)
        except Exception:  # noqa: BLE001 - same fail-closed class as the runner's FRONT_ERROR
            res.front_errors += 1
            continue
        h = store.message_hash(M.dedup_basis(rec, tr.hash_fields(rec.fields)))
        if h in seen:
            res.duplicates += 1
            continue
        seen.add(h)
        ev = pipe.evaluate(rec.fields, rec.header_people, check=M.split_check)
        thread = store.thread_key(rec.conversation_id)
        profiles = {s: Counter() for s in SECTIONS}
        totals, hits = [], []
        if ev.stripped is not None:
            ext = _external_words(rec.header_people)
            for name, text in ev.stripped.items():
                if name.startswith("attachment_"):
                    continue
                t, ch = R.unknown_cap_hits(text, ev.allowed)
                totals.append(len(ch))
                sep = t.find("\n\n") if name == "message" else -1
                for hit in ch:
                    sec = _SECTION_OF.get(name) or ("subject" if hit.start < sep else "body")
                    hits.append(WordHit(hit.word, hit.word.lower(), sec, hit.initial, _after_cue(t, hit.start),
                                        any(p in ext for p in hit.word.lower().split("-"))))
                if name == "message":
                    profiles["subject"] = R.caps_profile(text, ev.allowed, "subject")
                    profiles["body"] = R.caps_profile(text, ev.allowed, "body")
                    res.lower_seen.update(_LOWER.findall(t))
                else:
                    profiles[_SECTION_OF[name]] = R.caps_profile(text, ev.allowed)
        res.msgs.append(Scanned(thread, ev.reasons, ev.stripped is not None, profiles, totals, hits))
    return res


# ---- aggregation (memory only) ------------------------------------------------------------------------------------
@dataclass
class WordStat:
    key: str
    shown: str
    threads: set = field(default_factory=set)
    msgs: int = 0
    occ: int = 0
    sections: set = field(default_factory=set)
    initial: int = 0
    mid: int = 0
    cue: bool = False
    ext: bool = False
    lower: bool = False

    @property
    def flagged(self) -> bool:
        return self.cue or self.ext


def word_stats(res: ScanResult) -> dict:
    stats: dict[str, WordStat] = {}
    forms: dict[str, Counter] = {}
    for m in res.msgs:
        here = set()
        for h in m.hits:
            st = stats.setdefault(h.key, WordStat(h.key, h.word))
            forms.setdefault(h.key, Counter())[h.word] += 1
            st.occ += 1
            st.sections.add(h.section)
            st.initial += h.initial
            st.mid += not h.initial
            st.cue |= h.cue
            st.ext |= h.ext
            if h.key not in here:
                here.add(h.key)
                st.msgs += 1
                st.threads.add(m.thread)
    for k, st in stats.items():
        st.shown = forms[k].most_common(1)[0][0]
        st.lower = k in res.lower_seen
    return stats


def caps_only(res: ScanResult) -> list:
    """Messages whose only reason today is DENS_CAPS."""
    return [m for m in res.msgs if m.transformed and set(m.reasons) == {"DENS_CAPS"}]


def would_clear(msgs: list, allow: set) -> int:
    """Caps-only messages whose every unknown word is in `allow` (lower-case keys). Approximate: allowing a word also
    changes the cue check, so a rerun is the confirmation."""
    return sum(1 for m in msgs if all(h.key in allow or any(p in allow for p in h.key.split("-")) for h in m.hits))
