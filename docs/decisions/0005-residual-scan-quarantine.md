# ADR 0005: Residual scan, quarantine, eligibility and density check

Status: proposed (1 Nov 2026)
Relates to: ADR 0002 to 0004; design v0.5 section 5

## Context
The transform leaves some hits untreated (review-severity codes, unknown prefixes) and cannot see names the
NER layer misses. The pipeline needs a fail-closed last step that never emits partial output.

## Decision
- Residual scan, fail-closed. Detectors re-run over the output with approved tokens blanked. Each approved-format
  string must also be one the transformer emitted for that message, and ATT lines must match
  `ATT-n (ext, "label")` exactly. With NER on, NER and the name gate re-run over the output. Only DOB_KEYWORD is
  allowed (decision of 27 Oct 2026). Every other hit, including review-severity codes, quarantines the whole message.
- Quarantine (Z4): `quarantine/<keyed-hash>.q` holds the whole raw message under the key-store protector, plus
  `index.jsonl` with time, keyed hash, reason-code counts and version. The stripped attempt is never stored.
  A failed quarantine write stops the run (nothing is released). Retention is parked; `purge` is manual.
- Eligibility: body over 20,000 characters (`ELIG_SIZE`), or result-style wording together with a
  referral, sample, worksheet, QC or case number (`ELIG_RESULT_ID`), quarantines.
- Density check (the only net for names the NER layer misses): any unknown capitalised word (>=1), any run of
  5 or more digits, 3 or more `[CLIN]` markers. Unknown means not staff, config vocabulary, day/month, header
  participant, review-queue ignore term or common function word. Thresholds live in `residual.Limits`;
  `density.py` sweeps them.
- NER requested but not active: the pipeline refuses to run.
- The whole personal URL becomes `[URL]`. recall.py scores attachment filenames through the transform path.

## Consequences
- Synthetic corpus, NER off: 783 of 1,200 quarantined (65%), 0 planted identifiers in released output,
  5 released messages with a lowercase name fragment. NER-on figures are measured locally.
- Density cannot see lowercase names. Patient-name recall below 100% stays open.
- caps>=1 over-quarantines until the ignore list and config vocabulary are populated (one domain word was
  most of the clean-message quarantines on the corpus).
- Other external URLs, MONTH_YEAR, DATE_DOTTED and SIX_DIGITS now quarantine; none occur in the corpus.
- Tokens and contacts of quarantined messages are still written to the decode table (Z3 only).
- The corpus is self-authored, so these numbers are not independent evidence.

## Alternatives considered
- Format-only token acceptance: lets token-shaped input pass.
- One encrypted quarantine blob: rewritten on every message.
- Eligibility as audit-only: leaves no quarantine record.
- caps>=2: quarantines 0.4% of clean messages but misses a lone missed surname.
