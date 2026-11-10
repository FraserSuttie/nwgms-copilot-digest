# ADR 0016: Hyphenated ignore words, Surname First Directory names, review list

Status: proposed (10 Nov 2026)
Relates to: ADR 0003 (name gate), ADR 0006 (review queue and word files), ADR 0014 (Directory), ADR 0015 (ignore spans)

## Context
The session 13 review list on the real-mail sample (0.8.6-spans, Fraser's terminal; counts and word shapes only) showed
three gaps:
- The word files are loaded by splitting on anything that is not a letter, digit or apostrophe, so a hyphenated system
  name in the ignore file becomes separate words. The gate compares whole whitespace-separated words, so the hyphenated
  form never matched and stayed `[NAME-n]`.
- Directory names written Surname First in a body or forwarded header (as Trust display names are) were unknown full
  names. Only header participants were added in both orders (ADR 0009).
- `review.py list` hid names covered by the vocab file as well as the ignore file. Vocab words only pass the density
  check; the gate still masks them, so a masked word could be hidden from the list that exists to find it.

## Decision
- A word counts as an ignore word if it is an entry, or every hyphen- or punctuation-separated part of it is one
  (`ignored_word`). Used for span trimming and for place spans (ADR 0015).
- Directory two-token names are added in both orders, on one canonical name (as ADR 0009 does for headers). Tokens,
  nicknames and the organisation filter (ADR 0014) are unchanged.
- `review.py list` and `triage` hide names covered by the ignore file only. Names covered with help from the vocab
  file are shown, marked "vocab only". Coverage is hyphen-aware.
- Version 0.8.7-order.

## Consequences
- A patient whose name is a Directory name reversed passes, as a patient with the same name in normal order already
  does (ADR 0003). A patient sharing only one token with staff is unchanged.
- A hyphenated word passes if all its parts are ignore words, so short parts in the ignore file widen what passes:
  keep entries to whole system names.
- Real Directory (counts only): 583 people, 569 reversed variants added, name tokens 904, ambiguous tokens 124 (after
  the session 13 Directory edits). One person's reversed name equals itself.
- 7 new tests (`test_order.py`), invented names only; 6 fail without this change. Full suite 310.
- Synthetic corpus without NER unchanged apart from the version (472 released, 728 quarantined, 0 planted names
  released; regression set 62 / 38). NER run on the laptop to confirm 1,035 / 165 / 0 and the regression set 82 / 18 / 0.
