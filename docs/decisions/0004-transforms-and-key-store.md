# ADR 0004: Transforms, token format and local key store

Status: proposed (27 Oct 2026)
Relates to: ADR 0002 (content lane), ADR 0003 (name gate); design v0.4 section 9 and 12

## Context
Detector hits and gate decisions must become text that is safe to file in the vault, without giving
Claude any way back to the original. Some tokens must be reversible locally (Fraser needs to find a
referral again); patient identifiers must not be.

## Decision
Treatments (engine: `stripper/transform.py`):
- Patient identifiers: fixed placeholders, no map: `[PT-NHS] [PT-DOB] [PT-PHONE] [PT-ADDR] [PT-MRN] [PT-NUM] [PT-EMAIL]`.
  A full date near a DOB cue is `[PT-DOB]`; any other full date is `[DATE]` (date policy still open).
- REF/SMP/WS/QC/CASE: `<PREFIX>-` plus 8 consonant letters, truncated HMAC-SHA256 of `category|normalised value`
  under a per-year key. The year is the one inside the number (R26 means 2026); case numbers have no
  year and use the processing year. The alphabet has no digits and no vowels, so a token cannot trip a
  digit detector or read as a name. A collision gets a 2-character-longer code, recorded in the table.
- External contacts: `CONTACT-nnnn`, a counter in the key store. Only header correspondents (external
  From/To/Cc) are contacts; an external address that is not a header participant is `[PT-EMAIL]`.
- Attachment filenames: `ATT-n (type, "label")`. The label is an allowlist of generic words and
  CRF/DOC/version references; everything else is dropped. Original filename stays in the key store.
- Names: staff pass through the gate; others `[NAME-n]` (contiguous per message). GMC ids `[GMC-ID]`,
  personal URLs `[URL]`, NER places `[LOC]`.
- Clinical wording: `[CLIN]`. The whole sentence becomes `[CLIN]` when it has two or more clinical hits,
  a clinical hit and an identifier in the same sentence, or result-style wording (result words, variant notation).
- Untreated: review-severity codes (DOB_KEYWORD, URL_EXTERNAL, SIX_DIGITS, DATE_DOTTED, MONTH_YEAR)
  and unknown iGene prefixes are left in the text and counted. The residual scan must fail closed on them.

Key store (`stripper/keystore.py`), in `%LOCALAPPDATA%\nwgms-stripper` (local, not synced):
- `master.key.dpapi`, `tables.dpapi` and the review state `namegate-state.dpapi` are protected with
  Windows DPAPI via `ctypes` (current-user scope, no new dependency). There is no plaintext default;
  off Windows only the test-only protector exists, and tests use temp directories.
- Year keys are derived from the master, never stored. Message hashes are keyed.
- `audit.log` (plain): timestamp, keyed message hash, decision, counts by category, stripper version. No content.
- The slice 1 plaintext review state is migrated into the protected file and deleted on first open.
  `review.py purge` forgets unknown-name counts.
- `KeyStore.resolve()` is for the Fraser-run decode CLI and synthetic tests. The pipeline never calls it.

## Consequences
- Losing the Windows profile loses the decode tables and the master. Backup and retention are open.
- Result-style escalation drops whole sentences on benign prose ("positive feedback"); counted, not tuned.
- Without the NER model, unknown names are not detected, so they pass; the recall report and
  residual scan remain the controls. Transform tests cover deterministic categories only.
- `[NAME-n]` numbering restarts per text field; the email front end should pass subject and body as one string.
- Token validity depends on tokens never tripping a detector: tested over 15,000 generated tokens.

## Alternatives considered
- Plain files in a user-only folder relying on BitLocker: files readable by anything running as the user,
  with the master key beside the tables.
- Passphrase encryption: needs a cipher outside the standard library.
- Hex or base32 codes: digit runs can match PATIENT_NUMBER, SIX_DIGITS or MRN patterns.
- 6-character codes: about 26 bits, collisions expected around 8,000 values a year.
