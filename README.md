# nwgms-copilot-digest

Allowlist-based Copilot → Obsidian digest pipeline for NWGMS: schema, prompts, checker. Config and synthetic data only.

## Why

Morning and evening briefings, plus a reliable list of **replies I owe** and **replies I'm waiting on**, built from work email, Teams and meeting recaps without ever letting patient-identifiable or clinical content leave the Trust's Microsoft 365 tenancy.

## How (planned)

```
Outlook / Teams / meeting recaps
        │  (inside MFT M365 tenancy)
        ▼
 Copilot: allowlist-only digest prompt
        │
        ▼
 Deterministic validator ──fail──▶ quarantine (outside vault)
        │ pass
 ═══════╪═══════  trust boundary: content leaves the tenancy  ═══════
        ▼
 NWGMS-Vault/_Intake → Rule 9 tier screen → daily digest note
        │
        ▼
 _ActionLog.md updates + Claude daily briefing
```

## Phases

| Phase | What | Status |
|---|---|---|
| 0 | Design, schema, Copilot prompt v0, validator v0, compliance screen | 🔜 Starting 12 Oct 2026 |
| 1 | Manual runs: Fraser runs the prompt, the validator gates, Claude briefs | Not started |
| 2 | Automated via Power Automate / scheduled Copilot prompt, validator-gated | Not started (needs Phase 1 evidence) |
| 3 | Trust-approved Claude with DPA + direct Microsoft 365 connector | Aspirational |

## Rules

See [`CLAUDE.md`](CLAUDE.md). In short: **no real messages or digests in this repo, ever.** Synthetic fixtures only.
