# ADR 0009: Trust display names

Status: proposed (4 Nov 2026)
Relates to: ADR 0003 (name gate), ADR 0006 (email front end)

## Context
Run 8a (real mail, counts only) showed Trust participants masked as `[NAME-n]`. `display_name()` only swapped
"Surname, Forename". Shapes such as "Surname Forename (R0A) Manchester University NHS FT" or "Surname Forename MFT" kept
the organisation text in the name. The People entry was then keyed on the surname and "ft", the forename was not a
token, and the full name never matched. A digit disambiguator made `People.add` skip the person entirely. Shared
mailboxes were treated as people. `allowed_words` already allowed every header display word, so this was not the cause
of the density limit (1 unknown capital).

## Decision
- `display_name` strips a listed organisation suffix (`ORG_SUFFIXES`) and a trailing 1-2 digit disambiguator, in a
  loop, before the comma swap. It never reduces a name to nothing, and an unlisted suffix is left in place (fail closed).
- Trust header participants with a two-token normalised name are stored in both orders. Tokens stay on one canonical
  name and `canon` maps the other order to it, so a surname never reads as ambiguous. If the Directory already holds
  the person, the Directory entry stays canonical.
- Any other Trust display name (shared mailbox, three or more tokens) passes as an exact string only, with no tokens.
- ADR 0003 positions are unchanged: a forename alone passes only in a greeting, sign-off or speaker label, or when
  the full name passed in the same message.
- `ORG_WORDS` are always allowed in the density and cue checks, replacing the accidental allowance from header text.
- The cue check is unchanged. "Mrs <colleague surname>" is handled by the gate, not by CUE_NAME.
- Version 0.7.0-names.

## Consequences
- Colleagues' names, greetings and sign-offs in the Trust shapes now pass, and the review queue is no longer polluted.
- Expect NAME_SURVIVES and readability to improve. DENS_CAPS is expected to move little: it is driven by the limit of
  1 and by capitalised words outside the header, which is a separate slice.
- Accepted residuals: a patient with the same full name as a header colleague passes, as for the Directory. A bare
  patient surname equal to a colleague's passes if that colleague's full name passed in the same message
  (`BARE_WITH_FULL`). Three-token colleague names are masked.
- The synthetic corpus has no Trust shapes, so its numbers are unchanged (1,035 released, 165 quarantined, 0 planted
  names released; container, NER on). The unit tests with invented names carry the evidence.

## Alternatives considered
- First two tokens of any display name: breaks longer names.
- Swap every non-comma name: unsafe, since it cannot be told from forename-first.
- Pass a header forename mid-sentence: patient-forename collision risk.
