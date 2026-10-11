# CLAUDE.md: nwgms-copilot-digest

Standing instructions for any Claude session working in this repo.

## What this repo is

Design, configuration and code for an **allowlist-based** pipeline that turns Microsoft 365 Copilot summaries of Fraser's work email, Teams messages and meeting recaps into a tightly limited markdown digest. The digest is then filed into his NWGMS-Vault (Obsidian) for daily briefings and for tracking replies owed and awaited.

## The content rule (overrides everything)

This repo holds **Tier 0/1 material only**: schemas, prompt versions, validator code, flow exports, skill drafts, docs, and **synthetic** test fixtures.

It must **never** contain:

- a real digest, email, Teams message or meeting recap, even "redacted"
- patient-identifiable data, NHS numbers, iGene Live referral or sample numbers, results, or any clinical or genomic detail
- phone numbers, credentials, tokens, connection strings or tenant secrets
- contract values, supplier pricing, or evaluative commentary about named colleagues

Tier definitions follow the NWGMS-Vault `CLAUDE.md` §3. When unsure, treat content as the higher tier and leave it out.

## Design principles

1. **Allowlist extraction, not blocklist redaction.** Only pre-approved fields leave Copilot, and anything else is dropped.
2. **Fail safe.** A digest that fails validation goes to quarantine outside the vault, never into it.
3. **Defence in depth.** Constrained prompt → deterministic validator (no LLM) → vault Rule 9 intake screen.
4. **Human in the loop first.** Phase 1 is manual; automation comes only after the evidence says it's safe.
5. **The trust boundary is the file leaving the MFT M365 tenancy.** Every design doc must mark it.

## Working conventions

- Show proposed changes and commit messages to Fraser before committing.
- Small, meaningful commits; short ADRs in `docs/decisions/` for each design decision.
- Test fixtures are invented from scratch. Never derive one from a real message.
- Never send emails or Teams messages on Fraser's behalf.
