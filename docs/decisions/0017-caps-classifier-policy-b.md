# ADR 0017: Caps classifier and policy B (release and flag)

Status: proposed (10 Nov 2026)
Relates to: ADR 0005 (residual scan, density), ADR 0008 (cue check), ADR 0010 (caps diagnostic and vocab)

## Context
On the session 13 real-mail sample (0.8.7-order, counts only), 101 of 123 messages were held and DENS_CAPS (one
unknown capitalised word) was the only reason in 59. Fraser's leak reads found the held words were almost all ordinary.
DENS_CAPS is also the net for names NER misses, so it cannot simply be switched off. Fraser chose to tell a likely name
from an ordinary word by position, shape and nearby identifiers (options 3, 4 and 5 of five offered), and then policy B
over policy A (A: release only words in subjects and Title Case lines).

Measured before this decision (`capsclass.py`, read-only, and `pipeline.py --caps-policy`): on the real sample A would
release 35 more (57 of 123), B 50 more (72 of 123). On the synthetic corpus with NER, B released 8 messages, all with a
planted nickname ("mum says Xxxx is unwell"), so two shape signals were added before adopting it.

## Decision
- Each unknown capitalised word in a message is classified (`residual.classify_caps`, counts and fixed labels only):
  - position: SUBJECT or TITLE_LINE (neutral) or PROSE;
  - shape: TITLE, POSSESSIVE, PAIR (next to another unknown word or a `[NAME-n]`/`[PT-...]` token, not on a Title Case
    line), ALONE (only word on its line), GREETING, HEADER, NER (inside an NER person span), RELATIVE (within two words
    after a family word), ACTOR (no article before and a person verb after);
  - nearby: IDENT (REF/SMP/WS/QC/CASE, `[PT-...]`, `[GMC-ID]`, `[CLIN]` or `[CRED]` in the same sentence).
  A word with any shape or nearby signal is HOLD.
- Policy B: a message whose ONLY reason is DENS_CAPS is released when none of its unknown words is HOLD. NER must be
  active; with NER off the message is held as before. Every other reason still holds the whole message.
- The released block's flags carry `unknown words N` (a count) so the Rule 9 screen knows where to look; the run report
  prints messages and words released under the policy. `--caps-policy off` restores the old behaviour.
- Version 0.9.0-capsb.

## Consequences
- The control for a name that shows no signal (a single capitalised word in ordinary prose, no family word, no person
  verb, not marked by NER) moves from the density check to the Rule 9 screen, which reads every released block. To be
  stated in the test report and raised with IG (Design section 14).
- Synthetic, NER stub reporting active and finding nothing (worst case: every name the real NER strips stays in the
  text): 0 planted names, initials or strip items released in the main corpus or the regression set. Laptop run with
  NER to confirm the main corpus 1,035 / 165 / 0 and the regression set.
- ACTOR also holds an ordinary system name used as a subject without an article ("and Xxxx is down"); adding the word to
  the ignore file clears it.
- 8 new tests (`test_capspolicy.py`) plus the classifier tests; full suite 328.
