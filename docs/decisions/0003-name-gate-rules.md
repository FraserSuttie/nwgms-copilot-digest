# ADR 0003: Name gate rules

Status: proposed (27 Oct 2026)
Relates to: ADR 0002 (content lane via local deterministic stripper)

## Context
The stripper must keep staff names, so threads stay readable, and remove patient and external
names. Bare first names and surnames collide with patient names, and the NER layer misses some
names (lowercase, initials, nicknames).

## Decision
- Full names pass only via the People-Directory, header participants (From/To/Cc) with
  nhs.net or mft.nhs.uk addresses, or the local allowlist built through the review queue.
- A bare first name or surname passes only if it identifies exactly one known person AND sits in a
  salutation, sign-off or speaker-label position, or that person's full name also passed elsewhere
  in the same message. Ambiguous surnames and mid-sentence bare names are stripped.
- Directory entries pass whatever their organisation.
- Every other name becomes `[NAME-n]`: numbered per message, same name same number, one-way, no map.
- Known first names and surnames the NER layer misses are found by a directory token scan.
- Unknown names are counted in a local review queue (name and count, no surrounding text). The state
  directory is refused inside the repo or a OneDrive-synced folder.
- Acceptance metric: gate leaks (a patient, external or non-staff name passed) must be 0 on the
  synthetic corpus.

## Consequences
- The gate cannot rescue names the NER layer never finds; the residual rescan is therefore required.
- The bare-name collision risk is accepted only in greeting, sign-off and speaker positions.
- The review store holds names that may include patients' names. It is local-only, and its current
  plaintext form needs encrypting or purging (open item, transforms slice).

## Alternatives considered
- Full names only: strictest, but greetings such as "Hi Callum" would become `[NAME-1]`.
- Bare names pass anywhere when unique: convenient, but a patient surname equal to a staff surname would pass.
