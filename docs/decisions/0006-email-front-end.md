# ADR 0006: Email front end, batch runner and per-thread output

Status: proposed (1 Nov 2026)
Relates to: ADR 0002 to 0005; design v0.6 sections 4, 5, 7 and 10

## Context
The core (detectors, NER, name gate, transforms, residual scan, quarantine) takes fields and header participants.
Email needs a front end that turns one raw-drop file into those fields, plus a runner that files released
messages per thread and reports counts only. The Power Automate flow is built separately.

## Decision
- Raw-drop contract `nwgms-rawdrop/1`: one UTF-8 JSON per message with internet_message_id, conversation_id, sent,
  direction (worked out by the flow), from/to/cc/reply_to as {name, address}, subject, body and optional
  unique_body as {content_type: html|text, content}, attachments as {name, content_type, size, is_inline}. No Bcc.
  An unknown or missing key is a schema reject: counted, left in the drop, never quarantined.
- Normaliser (`stripper/mailfront.py`, standard library only): HTML to text (block tags as breaks, table cells
  joined with ` | `, style/script/head dropped), inline images counted and never kept. Graph `uniqueBody` is used
  when present (option A1); the marker cut still runs over it. Quoted history and forwards are cut at the first
  marker (Outlook reply div, blockquote, From+Sent block, Original Message, "On ... wrote:", `>` lines, forward
  banners) and never parsed. Disclaimer and banner paragraphs are dropped by marker phrase. Signature: cut at
  `-- `, the Outlook signature div or "Sent from my"; in the last 12 non-empty lines a stand-alone sign-off keeps
  itself and one name line (B1). Encoding: BOM, CRLF, zero-width, soft hyphen, NFKC; cp1252 mojibake repaired only
  when the repair reduces the markers (C1). RE/FW prefixes are dropped from the subject; FW is flagged.
- Message record: `header` (From/To/Cc lines; Trust-domain participants by display name, external participants by
  address, which the core turns into CONTACT-nnnn), `message` (subject + blank line + body, one string per ADR
  0004), `attachment_1..n` (non-inline only). `header_people` are From, To and Cc with "Surname, Forename (Org)"
  turned into "Forename Surname". Reply-To is shown only as none / same as sender / differs (n) (D1).
- Shape check: after stripping, the header must still have its three lines and the message must still split at
  the first blank line; otherwise the whole message is quarantined (`FRONT_SHAPE`). A front-end exception parks
  the raw object in quarantine (`FRONT_ERROR`).
- Thread key: `THR-` + 8 consonants, keyed HMAC of the Graph conversation ID under its own derived label.
- Message hash (dedup): keyed hash of the normalised fields + sent time to the second + conversation ID, so a
  message seen in two folders collapses but two identical short replies do not. Decisions are kept in
  `seen.jsonl` in the local state (keyed hashes only). Released and quarantined hashes are skipped;
  `--retry-quarantined` reprocesses quarantined ones. Before appending, the thread file is checked for the block's
  end marker, so a crash between append and the seen record does not write twice.
- Output: `<out>/Comms/_Messages/THR-*.md`, one append-only file per thread, frontmatter on creation, one write
  per block, start and end markers (a start without an end is reported as a torn block). Instead of a per-message
  copy into `_Intake` (design section 5, step 6), each run writes one counts-and-hashes batch manifest to
  `<out>/_Intake/comms-batch-<UTC>.md` for Rule 9 batch mode (E2).
- Runner (`stripper/runner.py`, run by Fraser only): NER always required. Paths: drop may be OneDrive (design Z2)
  but not the repo or the vault; state (key store, review state, quarantine, seen index) and the vocab and ignore
  files must be local, outside the repo and OneDrive; out may be OneDrive but not the repo, and not the vault
  while the lane is inactive unless `--lane-active` (F1). Drop files are never modified. Errors print the
  exception type only.
- Vocabulary: `--vocab` and `--ignore` are read with `load_wordlist`; vocab goes to `Pipeline(vocab=)`, ignore
  words are combined with the review-state ignore list. `review.py list|triage --vocab --ignore` hide names those
  files already cover. The files stay the source of truth; nothing is imported into the state (G1).
- Core fix found by this slice: the density check now allows only Trust-domain header participants. Before, an
  external participant's display name in the body passed density when NER missed it.

## Consequences
- Synthetic raw drop (seed 20261027, 1,200 emails plus 36 duplicate files, NER off, container): 528 released,
  672 quarantined (DENS_CAPS 620, ELIG_RESULT_ID 94), 36 duplicates skipped; 0 planted strip values, 0 name
  fragments and 0 quoted-history plants in released blocks. NER-on figures are measured locally (Run 4).
- With NER off, DENS_CAPS is mostly real names the NER layer would have caught; it is not a real-mail rate.
- Title-case subjects and table header cells raise DENS_CAPS on real mail until the vocabulary is populated.
- Forwarded content is dropped, not fetched; if the original is not in the mailbox it is lost (by design).
- Exchange can start a new conversation when a subject is edited; subject matching stays a fallback, not built.
- The lowercase-name gap is unchanged. Removing signatures and history reduces the text it applies to.
- Raw-drop, quarantine and seen-index retention remain open; nothing is purged.
- The corpus is self-authored, so these numbers are not independent evidence.

## Alternatives considered
- Body only, no uniqueBody (A2): the marker cut alone carries quoted-history removal.
- Signature markers only (B2): fewer false cuts, more quarantines from titles and departments.
- Content-only message hash: collapses distinct identical replies (found in testing).
- Per-message copies into `_Intake` (E1/E3): doubles content in the vault for no screening gain.
- A local drop with a landing move: not needed; the drop stays in OneDrive per design Z2.
