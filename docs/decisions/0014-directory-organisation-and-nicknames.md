# ADR 0014: Directory organisation filter and bracketed nicknames

Status: proposed (9 Nov 2026)
Relates to: ADR 0003 (name gate), ADR 0009 (Trust display names), ADR 0010 (caps vocabulary)

## Context
The Run 10 leak read (real mail, counts and shapes only) found two external contacts' names in clear in one block.
Both were rows in the People Directory, which the name gate read as staff whatever their organisation; the header
had already tokenised the same people as CONTACT-n. A bracketed nickname in the Directory was not a name token, so a
nickname had been added to the local vocab file instead, which made it pass for anyone.

## Decision
- `parse_directory` reads the Organisation column. Only rows starting with NWGMS or MFT are staff. Other trusts,
  suppliers, national bodies and TBC rows are left out, so their names are treated as unknown (NER, the gate and
  the density check). A table with no Organisation column is read as all staff (older format).
- A round-bracketed single-word nickname (invented example: `Marisolde (Mari) Vantrell`) is added as an alias of its
  row: `mari vantrell` resolves to the same person and `mari` becomes a name token, so the bare nickname follows the
  usual position rules. Square-bracketed notes such as `[surname unknown]` are dropped.
- Staff nicknames are removed from the local vocab file (Fraser, locally). The vocab file is for ordinary words only.
- External people stay `[NAME-n]` in the body (not CONTACT-n); revisit only if threads become hard to follow.
- Version 0.8.4-dirorg.

## Consequences
- More real-mail quarantines are expected (DENS_CAPS, CUE_NAME) where external contacts are named in the body.
- A staff member whose row says TBC is treated as unknown until the row's organisation is corrected.
- Real Directory (counts only): full names 613 to 591, name tokens 927 to 899, nickname aliases 0 to 4, ambiguous
  tokens 139 to 128.
- Synthetic corpus and gate recall unchanged without NER. NER run on the laptop to confirm 1,035 / 165 / 0.
