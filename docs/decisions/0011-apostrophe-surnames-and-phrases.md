# ADR 0011: Apostrophe surnames and the phrase allowlist

Status: proposed (4 Nov 2026)
Relates to: ADR 0010 (caps diagnostic), ADR 0009 (Trust display names)

## Context
Two gaps found while clearing DENS_CAPS on the frozen real drop (counts only):
- A Directory surname with an apostrophe (invented example: `O'Zorblax`) is stored as one token `o'zorblax`, but the
  caps scan sees only the tail after the apostrophe (`Zorblax`), so a staff name in the Directory was still counted
  as an unknown capital.
- A common word that is also a possible name (invented example: `Vrimble`) cannot be added to the vocabulary without
  allowing it everywhere. The reliable signal is the adjacent pair (`Vrimble Court`), which marks the place, not a person.

## Decision
- An unknown capital directly after `<word>'` or `<word>’` is allowed when the whole apostrophe token is in the
  allowed set. Curly and straight apostrophes are normalised in every allowed-word source. The tail alone is still counted.
- A vocabulary or `--phrases` entry containing a space is a phrase. Both words pass only when adjacent, in order,
  separated by a single space on one line (case-insensitive). Either word alone is still counted. Phrase words are never
  added to the single-word vocabulary.
- `--phrases F` (one phrase per line, two or more words, otherwise refused with a message that does not echo the line)
  on `runner`, `capsdiag` and `capsvocab`. The file must be outside the repo, OneDrive, a vault and the drop.
- Version 0.8.1-caps. The limit stays 1.

## Consequences
- A phrase entry exempts its pair wherever it appears, including a person who shares the pair. Keep phrases to places
  and products, not names.
- Allowing a Directory entry still makes its name tokens allowed (ADR 0009 residual, unchanged).
- Synthetic corpus unchanged: 1,035 released, 165 quarantined, planted names released 0.
