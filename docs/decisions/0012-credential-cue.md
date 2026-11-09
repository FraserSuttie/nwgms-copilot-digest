# ADR 0012: Credential cue

Status: proposed (9 Nov 2026)
Relates to: ADR 0004 (transforms), ADR 0005 (residual scan)

## Context
The Run 10 leak read (real mail, 0.8.1-caps, counts and shapes only) found a temporary password in clear in a
released block, and a username after a label in the same thread. No detector looked for credentials: a password
with no digits, or digits too short for the number rules, passed every check. Credentials are Tier 3 (vault
CLAUDE.md section 3).

## Decision
- New detector code `CREDENTIAL` (fail severity), placeholder `[CRED]` (fixed, no map, nothing in the key store).
- Cue: password, passwd, pwd, passcode, passphrase, pin, username, user name, user id, login, log-in id, with an
  optional temporary / temp / new / one-time / initial in front. Case-insensitive.
- Value: a quoted string of up to 40 characters within 20 characters of the cue (double quotes, straight or curly, or
  a single quote after a space or separator, so an apostrophe never opens a quote); otherwise one token after `:`,
  `=`, `-` or `is`. Trailing sentence punctuation stays outside the span.
- Credentials claim their span before every other detector, so a password made of digits becomes `[CRED]` rather
  than `[PT-MRN]` or `[PT-NUM]`.
- Version 0.8.2-cred.

## Consequences
- Fail-safe over-redaction: an ordinary word after "password is" or "username:" is also replaced (invented example:
  "the password is reset" loses "reset"). Accepted.
- Not covered: a credential with no cue word, or a cue more than 20 characters before the quote. The residual scan
  and the leak read remain the backstop.
- A placeholder in the output says a credential was present; it does not say whose.
- Synthetic corpus unchanged without NER (472 released, 728 quarantined, 0 planted names released). NER run on the
  laptop to confirm 1,035 / 165 / 0.
