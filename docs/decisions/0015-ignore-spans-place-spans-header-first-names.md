# ADR 0015: Ignore-list spans, staff names tagged as places, header first names

Status: proposed (10 Nov 2026)
Relates to: ADR 0003 (name gate), ADR 0004 (transforms), ADR 0005 (residual scan), ADR 0009 (Trust display names),
ADR 0014 (Directory nicknames)

## Context
The session 13 over-redaction read (real mail, 0.8.5-credfix, after the A3 ignore and vocab pass; Fraser read it, no
Tier 3) found three causes of over-redaction, all in code:
- The ignore file did not work on multi-word spans. The gate passed a span only if the whole normalised span was an
  ignore entry, but the file is loaded as single words. A system or mailbox name that NER joined to the next word stayed
  `[NAME-n]`.
- NER place hits never reached the gate, so a staff sign-off or an ignore-list word that spaCy tagged as a place became
  `[LOC]` whatever the Directory or the ignore file said.
- A bare first name shared by several staff was always ambiguous, so greetings to colleagues with common first names were
  masked even when that colleague was on the message.

## Decision
- **Ignore spans.** Ignore-list words are trimmed from both ends of a name span. A span made only of ignore words passes
  (`IGNORED`); the rest of a partly ignored span is gated as usual (invented example: `Zentrix Down` keeps `Zentrix` and
  gates `Down`). A whole-span ignore entry still passes as before.
- **Place spans.** An NER place span whose every word is a staff-name token (Directory, header) or an ignore word is a name
  candidate for the gate, not a `[LOC]`. The same rule runs in the transform and in the residual scan. Other places are
  unchanged.
- **Header first names.** A bare first name or Directory nickname shared by several staff counts as one person when it
  is the first name of exactly one Trust-domain header participant of this message. The position rules still apply
  (greeting, sign-off, speaker label, or that person's full name passed). Surnames stay ambiguous; external header
  participants do not count.
- Version 0.8.6-spans.

## Consequences
- Residual risks, the same kind as ADR 0009: a patient whose first name matches a header colleague's and appears in a
  greeting or sign-off position passes; a patient with a staff-name token tagged as a place is gated as a name (it may
  pass in those positions) instead of becoming `[LOC]`. A word that should never pass must not go in the ignore file.
- First version of the header rule also passed an ambiguous surname ("Hi Ridgeway," in the synthetic corpus, 4 gate
  leaks and 1 released message). Narrowed to first names before commit; the corpus tests are unchanged.
- 13 new tests (`test_spans.py`), invented names only; 9 fail without this change. Full suite 303.
- Synthetic corpus without NER unchanged apart from the version (472 released, 728 quarantined, 0 planted names
  released; regression set 62 / 38). NER run on the laptop to confirm 1,035 / 165 / 0 and the regression set 82 / 18 / 0.
