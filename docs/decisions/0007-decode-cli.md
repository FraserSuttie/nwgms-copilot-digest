# ADR 0007: Local decode CLI

Status: proposed (2 Nov 2026)
Relates to: ADR 0004 (transforms and key store), ADR 0006 (runner); design v0.7 sections 9, 11 and 12

## Context
REF, SMP, WS, QC, CASE, CONTACT and ATT tokens are reversible, but only locally (design section 9). Fraser needs a
way back to the originals that Claude never runs and whose output never lands in a file. A gap in slice 4 also
had to be closed: ATT originals are keyed by the pipeline's field hash, but thread blocks show the runner's
dedup hash, so a block could not find its own attachments.

## Decision
- `stripper/decode.py`, run by Fraser only: `decode.py TOKEN [TOKEN ...]` or `decode.py --thread THR-*.md` (A1).
  It never rebuilds message text.
- Decodable: REF, SMP, WS, QC, CASE (normalised value, no separators), CONTACT-nnnn (`Name <email>`), and
  `ATT-n@HASH`, where HASH is at least the 12 hex characters in the block heading. A prefix that matches more
  than one block is refused as ambiguous.
- Not decodable (no map, by design): `[PT-*]`, `[NAME-n]`, `[CLIN]`, `[DATE]`, `[LOC]`, `[URL]`, `[GMC-ID]` and `THR-`
  keys. Malformed and unknown tokens are refused. A refusal never contains a value.
- ATT link (B1): when the runner releases a block it records block hash -> field hash in the key store
  (`blocks`). Decode follows the link. Quarantined messages are never linked, so their ATT tokens are refused.
- `--thread` prints `token | kind | value` for each decodable token in first-appearance order, ATT rows qualified
  by block hash, and attachment filenames with no extra flag. A footer gives counts only for the placeholders that
  cannot be decoded and for refusals (E1). Message text is never printed.
- Terminal only (R1): if stdout is not a terminal (redirect or pipe), the run is refused before the key store is
  opened. Nothing is written to disk except the audit line. No clipboard.
- Audit (T1): one line per run in `audit.log`: timestamp, `event: decode`, mode, the tokens asked for, counts
  resolved by prefix, refused count, version. Never a value. Tokens already sit in plain text in the vault, so
  listing them adds an access trail without adding content.
- Paths: `--state` must be local (not the repo, not OneDrive), default `%LOCALAPPDATA%\nwgms-stripper`, and must
  already hold a key store (decode never creates one). `--thread` must be an existing `THR-XXXXXXXX.md` with the
  stripper's frontmatter, not inside the repo, at most 5 MB; OneDrive and the vault are allowed (stripped content).
- Exit codes: 0 all resolved, 1 something refused, 2 usage, path or terminal error. Version 0.5.0-decode.

## Consequences
- Design section 11 ("writes to a file or clipboard") is superseded: terminal only. Needs a design v0.8 edit.
- Blocks written before this change have no link, so their ATT tokens cannot be decoded (synthetic runs only).
- ATT originals of quarantined messages still sit in the key store; retention stays parked with the others.
- Terminal-only is enforced against redirects, not against copy from the terminal or screen capture.
- Tests (synthetic, throwaway key stores, test-only protector): round trips for every decodable prefix, an end-to-end
  thread table through the runner, refusals, the redirect refusal, the audit line, and a check that no body line
  of the thread file appears in the table. 186 tests pass in the container (2 NER tests skipped there).

## Alternatives considered
- Subcommands (`decode tokens` / `decode thread`), like `review.py`: more typing for no gain.
- Re-keying ATT originals to the block hash at release (B2): leaner table, but needs a pipeline change to drop
  quarantined originals and changes ADR 0004 behaviour.
- Allowing redirected output: makes "never writes to disk" a convention only.
- Counts-only audit: no record of which token was looked up.
