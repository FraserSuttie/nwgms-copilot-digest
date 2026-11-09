# ADR 0013: Cued order numbers and malformed dates

Status: proposed (9 Nov 2026)
Relates to: ADR 0012 (credential cue), ADR 0004 (transforms)

## Context
The Run 10 leak read (real mail, 0.8.1-caps, counts and shapes only) found a 4-digit order number in clear after
the words "order number", and a date with a doubled separator (invented example: `07//03/19`) that no date pattern
matched. Short numbers are not caught on their own because times, years, alert IDs and test codes look the same,
and DENS_DIGITS is already the largest remaining quarantine reason.

## Decision
- Order-style numbers: 3 to 12 digits with an optional 1 to 3 letter prefix, only directly after a cue: order
  (number / num / no / # / id), orderID, request no/id, accession no, episode no, encounter id, lab no. Existing
  code `PATIENT_NUMBER`, placeholder `[PT-NUM]`; no new placeholder. A full stop ends the number unless a digit follows.
- Malformed dates: day, month, two- or four-digit year where each separator holds a `/`, `-` or `.` and at least
  one separator is two characters (`//`, ` /`, `-/`). Existing code `DATE`, so the DOB-cue rule still gives `[PT-DOB]`.
  Runs after the validator patterns, so well-formed dates are unchanged.
- `validator/patterns.py` is not changed (it also serves the Copilot digest validator).
- Version 0.8.3-numdate.

## Consequences
- A number after a cue word that is not an identifier is also replaced (invented example: "order 2244 failed").
  Accepted.
- Not covered: an order number with no cue word, a cue separated from the number by other words, or a date with
  only spaces between its parts. The residual scan, DENS_DIGITS and the leak read remain the backstop.
- Synthetic corpus unchanged without NER (472 released, 728 quarantined, 0 planted names released). NER run on the
  laptop to confirm 1,035 / 165 / 0.
