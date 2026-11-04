# ADR 0010: Caps diagnostic, local vocabulary builder and caps limit

Status: proposed (4 Nov 2026)
Relates to: ADR 0005 (density check), ADR 0009 (Trust display names)

## Context
On the frozen real drop (97 files, 95 messages) Run 9 released 12 and quarantined 83. DENS_CAPS was in 83 messages and the
only reason in 67, and ADR 0009 did not move it. The check counts "unknown capitalised words" per output field after the
transform, and `Limits.unknown_caps` is 1, so one such word in any field quarantines the whole message. The count is a
regex (`[A-Z][a-z]{2,}`, optionally hyphenated) minus allowed words (days, months, organisation words, vocabulary, staff
tokens, ignore list, Trust header display words), openers, and a sentence-initial word followed by lower-case prose. It
therefore counts Title Case subjects, table headers, bare words alone on a line, bullet items, product, department and
supplier names, and any name the gate or NER left behind. How the 83 split across those shapes could not be seen from
the printed counts, and the vocabulary that would clear them had to be built blind.

Two further findings from reading the code:
- The pipeline applies the limit per field, while the `density.py` sweep sums across fields. They agree at 1 and differ
  above 1 (the pipeline is looser).
- `density.py` on the synthetic corpus (NER on) shows what limit 1 protects, not what it costs: every clean synthetic
  message has zero unknown capitals because the corpus vocabulary covers all its words. Caps >= 1 catches 16 of 28 messages
  with a surviving name fragment (the other 12 are lower-case names, which no caps check can see), caps >= 2 catches 8,
  caps >= 3 catches none. Through the real pipeline, limit 2 releases 8 more messages and all 8 carry a surviving nickname.

## Decision
- `Pipeline.evaluate()` runs every check and returns reasons, counts, the stripped attempt (memory only) and the allowed
  set, with no quarantine, audit or key-store write. `process()` is `evaluate()` plus the decision and behaves as before.
- `residual.unknown_cap_hits()` is the single definition of the count; `unknown_cap_words` and `unknown_caps` are views
  of it. `caps_profile()` returns shape counts only (initial, mid-sentence, hyphenated, possessive, run of 2 or 3+, isolated,
  alone on a line, Title Case line), by subject, body and header field.
- `capsdiag.py`: read-only, counts only, no word, no file written. Reports outcome groups, the unknown-cap histogram,
  the release curve by limit (per field and per message total), section and shape splits, overlap with other word sets,
  distinct words, frequency of frequency by messages and by threads, and a vocabulary coverage curve (top N words by
  thread count, and words also seen in lower case). It uses an existing baseline state read-only and refuses to create one.
- `capsvocab.py`: Fraser's terminal only. Words are ranked by thread count, not message count, so a patient surname
  repeated in one thread does not look like a product name. Flags: also seen in lower case, one thread only, within two
  words after a patient cue, word of an external header participant. Cue and external words are listed last and need the
  word typed in full to allow. Stdout and stdin must be terminals. `--list` prints and writes nothing. Otherwise the only
  write is an append of the allowed word to `--vocab`, which must be local (not the repo, OneDrive, a vault or the drop).
  It prints a projected count of messages that would clear (approximate, since allowing a word also changes the cue check).
- `runner.py --caps-limit N` (default 1, so behaviour is unchanged). At 1 the rule stays per field; above 1 the limit counts
  the message total, which matches the sweep. The report prints the limit used.
- Version 0.8.0-caps. No default changes.

## Consequences
- Fraser can see what DENS_CAPS is catching on real mail as numbers, then build `vocab.txt` from his own terminal.
- A word added to the vocabulary is no longer checked in any message, so adding a surname opens the net for that name.
  The thread ranking and the flags reduce that risk; they do not remove it. Fraser reads every word he allows.
- Raising the limit above 1 is a separate decision, to be taken from the real-mail release curve. Synthetic evidence says
  limit 2 would release nickname survivors.
- The diagnostic and vocabulary tool re-run NER over the drop; both need the NER layer active.
- The synthetic corpus numbers are unchanged from Run 6 and Run 9 (1,035 released, 165 quarantined, 0 planted names released).

## Alternatives considered
- A runner flag for the diagnostic: reuses the loop but writes quarantine, seen and output on every pass.
- Reading the quarantine index: holds reason counts only, so no shape histogram is possible.
- Print-only vocabulary tool: stronger write guarantee, rejected by Fraser in favour of interactive accept to a local file.
- Raising the default limit now: loses the nickname survivors on the synthetic corpus and is not supported by real-mail data yet.
