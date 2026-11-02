# ADR 0008: Cue-based name check

Status: proposed (2 Nov 2026)
Relates to: ADR 0004 (transforms), ADR 0005 (residual scan and density); design v0.9 section 15 (patient-name recall)

## Context
With NER on, 12 of 1,058 released synthetic messages still carried a lower-case patient name (Run 3). Density only
counts capitalised words and NER misses lower-case names, so nothing caught them. The patient-name pass mark is not
met and the lane stays inactive (vault CLAUDE.md section 8). Parked options were an out-of-vocabulary check,
cue-based quarantine and allowlist-only output. Fraser chose cue-based quarantine on 2 Nov 2026.

## Decision
- `residual.cue_check`, run on every non-attachment output field after density. Any hit quarantines the whole
  message (`CUE_NAME`, counts only). Fraser's choices: quarantine, not replace (1A); fixed cue list (3A).
- Cues (case-insensitive): patient, pt, mr, mrs, ms, miss, mx, baby, proband.
- Window: the 2 words after the cue. A colon or dash straight after the cue is a label and is skipped; a full stop
  is skipped only after the abbreviations pt, mr, mrs, ms, mx. The window stops at sentence punctuation, a bracket,
  a newline, or an approved token or placeholder (already treated).
- Recognised (Claude's proposal; Fraser left this to Claude): words in `CUE_FOLLOW`, a fixed list of general English
  and admin words written from general usage, not from the corpus; words in `allowed` (staff tokens, config
  vocabulary, ignore list, Trust-domain header names, days and months); words with digits. A possessive 's is
  ignored. Anything else is unrecognised. There is no dictionary file and no new dependency, so the check fails closed:
  a benign word missing from the list quarantines, and Fraser adds it to his local vocabulary or ignore file.
- Version 0.6.0-cue.

## Consequences
- Synthetic corpus, NER on (container, 1,200 messages, seed 20261027): released messages with a planted name
  fragment 12 -> 0; quarantine 142 -> 165 (11.8% -> 13.8%). All 30 CUE_NAME messages held a real planted name (12
  lower-case, 12 initials, the rest capitalised names density also caught); none was a clean message.
- That 0 is flattering: the generator plants lower-case names with one template ("seen pt <name>"). `test_cue.py`
  adds other cues, positions, punctuation and clean prose that the generator does not produce. Writing those found
  two real gaps, now fixed: "Patient: <name>" passed (the colon ended the window) and a full stop after "patient" was
  read as an abbreviation.
- Known misses, kept as tests: a name with no cue next to it ("sample for jo kent") and relative cues outside the
  list ("mum says ivo"). The out-of-vocabulary check stays parked for these.
- The false-positive rate on real mail is unknown; `CUE_FOLLOW` and the local vocabulary file will decide it. The
  counts-only real-mail baseline should be run before and after this change.
- Self-authored tests, not independent evidence. 199 tests pass in the container (1 skipped).

## Alternatives considered
- Replace the following word with `[NAME-n]` and release (1B): keeps more mail, but depends on getting the word
  boundary right on unseen text.
- Configurable cue list in the local vocabulary file (3B): flexible, but the safety rule would then live in an
  unversioned local file.
- A bundled English dictionary as "recognised": fewer false positives, but a large file or a new dependency, and
  real names that are also English words ("rose", "mark") would pass.
