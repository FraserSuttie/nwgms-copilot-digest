# Copilot to Vault Digest: Spec v0.1

**Owner:** Fraser. **Date:** 26 Oct 2026. **Status:** approved to write; v1 build starts from this document.

## 1. Purpose

A second brain that knows where my work is up to. Claude uses it to brief me, track the replies I owe, suggest drafts (I always review and send manually from Outlook) and feed `_ActionLog.md`. The Option 2 build described here is the foundation. Later lanes add sources, each with its own addendum.

## 2. Principles

- Allowlist extraction, not redaction. Anything that does not fit a field is dropped.
- A deterministic validator decides what passes. No LLM is the safety check.
- A failing file goes to quarantine outside the vault, never into it.
- I promote files into `_Intake` by hand until Phase 1 shows it is safe to automate.
- Raw mail, Teams text and recaps never leave the tenancy. The trust boundary is the moment a file lands in the vault.
- One schema per source. A new source needs an addendum before it is built.
- Nothing is ever sent on my behalf.

## 3. Scope

**In v1**
- Lane A: email in my ACTION folder (B2).
- B1: scheduled Copilot prompt for Teams, best effort and labelled non-exhaustive.
- B4: meeting recaps via the existing redaction queue, hand-redacted.
- Validator, state file, briefing format.

**Later, each with an addendum**
- Lane B: inbox-wide with a cheap pre-filter and a lighter schema.
- Lane C: replies awaited, for example via a flag or category on Sent Items.
- Calendar, a draft-reply loop (Outlook drafts only, never sends), a Copilot Studio agent.

## 4. Architecture

```
INSIDE THE TRUST (tenancy, OneDrive, laptop)

 Outlook ACTION ──▶ B2 flow + AI call ──┐
 Teams messages ──▶ B1 Copilot prompt ──┼──▶ G0·G1 ──▶ Staging folder ──▶ G2 validator ──pass──┐
 Meeting recaps ──▶ B4 hand redaction ──┘             (outside vault)     (laptop, Python)      │
                                                              │ any fail                         │
                                                              ▼                                  │
                                                  Quarantine (outside vault)                     │
═══════════════ TRUST BOUNDARY: a file lands in the vault ═══════════════════════════════════════│═
 VAULT + CLAUDE (consumer account)                                                               ▼
 _Intake ──▶ G3 Rule 9 screen ──▶ filed notes + state file ──▶ Claude reads the state file
                   │ stop
                   ▼
        Held in _Intake, flagged to Fraser, not filed
```

## 5. Digest schema v1

One digest file per run. One entry per thread. Only these fields.

| Field | Allowed content | Tier |
|---|---|---|
| `source` | email, teams-chat, teams-channel, meeting-recap | 0 |
| `received` | date and time | 0 |
| `counterpart` | name and role or team, resolved against People-Directory, max 60 characters | 1 |
| `systems` | fixed list: iGene, Clarity, DLIMS, StarLIMS, Congenica, Hive/Epic, TIE, Monday.com, Other | 0 |
| `refs` | CRF-YYYY-NNN, Genial ticket (`#NNNNN` when first raised), Jira ticket (`IG-NNNNN` once Genial adopts it), Monday item ID, Q-Pulse DOC number only | 1 |
| `request_type` | fixed list: access request, config change, incident/outage, testing/UAT, report/query, scheduling, governance/SOP, FYI | 0 |
| `action_needed` | fixed list: reply, review, approve, attend, none | 0 |
| `direction` | owed-by-me (Lane A is always this); later awaited-from-them, fyi | 0 |
| `due` | date, if one is stated | 0 |
| `topic` | matching `Meetings/_Topics/` initiative, if obvious | 0 |

Dropped in v1: `subject` and free-text `gist`. Unknown values become `Other` or empty. No referral, sample, patient or MRN number, from any environment.

## 6. Gates

- **G0, prompt.** Returns JSON with the allowlisted fields only.
- **G1, in-flow.** Fixed-list membership, length caps, entry-count cap. Power Automate probably has no regex function (to confirm), so pattern checks sit in G2.
- **G2, Python validator** (standard library only, runs on the laptop). Checks: NHS numbers with mod-11, UK phone numbers, dates of birth, postcodes, email addresses outside nhs.net and mft.nhs.uk, iGene identifier patterns (section 7), any field outside the schema, size caps. Non-zero exit sends the file to quarantine.
- **G3, Rule 9 intake screen** in the vault.
- **Audit and stop.** A counts-only log. Kill switch: turn the flow off.

## 7. iGene identifier patterns (for G2)

Source: the Live Nomenclature preference screens, read 26 Oct 2026. Year is two digits (YY). Base-34 means the digits 0-9 and the letters A-Z except I and O.

| Identifier | Composition | Pattern |
|---|---|---|
| Sample # | user code (S normal, D derived) + YY + `-` + 4 base-34 | `[SD]\d{2}-[0-9A-HJ-NP-Z]{4}` |
| Lab Referral # | `R` + YY + `-` + 4 base-34 | `R\d{2}-[0-9A-HJ-NP-Z]{4}` |

Sample # and Lab Referral # are confirmed from their composition screens. Patient # is confirmed too (an autonumber padded with zeros to 7 digits). The others are known only from the Example column of the Nomenclature list and are not confirmed, so they are broad, flagged patterns:

| Identifier | Shape seen | Handling |
|---|---|---|
| Other prefixed IDs (test, fetus, report, clinical report, culture) | one or two letters + YY + `-` + 3 to 5 alphanumerics, optional `-x` | `[A-Z]{1,2}\d{2}-[0-9A-Z]{3,5}(-[0-9A-Z])?` fails |
| Worksheet, QC ID | `WS` + YY + `-` + 5 digits; `QC` + YY + `-` + 4 digits | fails |
| Case # | `YYYY/NNNN` | fails |
| Patient # (confirmed) | autonumber, zero-padded to 7 digits | `\b\d{7}\b` fails |
| Clinical Referral # and other 6-digit padded numbers | 6 digits | `\b\d{6}\b` held for review, because it collides with ticket numbers |

Allowed `refs` shapes are checked before these rules: `CRF-\d{4}-\d{3}`, `#\d{4,6}` (Genial), `IG-\d{4,6}` (Jira), `DOC\d{4,5}` (Q-Pulse). Anything else in `refs` is dropped.

In Phase 1 anything held for review is treated as a fail and logged as a possible false positive, so the patterns can be tuned on evidence. Test and Sandbox identifiers are blocked as strictly as Live.

## 8. Lane A flow

- Scheduled, weekdays only, at **07:30, 12:30 and 16:30** UK time.
- Lists the ACTION folder. It does not use a "new email" trigger, because that trigger works on received date and can skip mail that has been moved in.
- One AI call per email. Processed mail is tagged with the Outlook category "Digested" so it is not processed twice. This changes the category only.
- One JSON file per run goes to the staging folder.
- **B1:** a scheduled Copilot prompt on the same schema. Output route to be confirmed by testing. In Phase 1, I copy and paste.
- **B4:** recaps go to the redaction queue (outside the vault), I redact by hand, and they then join staging and the validator.

## 9. Laptop gate

A Python script runs on the laptop, in a plain `C:\Users\Fraser.Suttie\...` folder, not in OneDrive. It validates staging, moves failures to quarantine, and moves passes to `_Intake`. In Phase 1, promotion is a command I run by hand. It also builds the state file: open items, their ages, and a diff against the previous run.

## 10. State file and briefing

The state file has a fixed small size (target 3 KB or less) and contains deltas only. Ageing and diffs are computed by the script, so Claude's tokens go on judgement: priorities, what changed, links to initiatives and to `_ActionLog.md`. A morning briefing covers what is new, urgent and aged. An evening briefing covers what changed. Digests are retired once filed.

## 11. Failure modes

| Failure | Expected behaviour |
|---|---|
| AI returns free text | G1 or G2 catches it; file is quarantined |
| False positive | Logged; patterns tuned on evidence |
| Flow fails silently | The script warns if a scheduled run left no file |
| Duplicate processing | The "Digested" category |
| Laptop off | Staging accumulates; promoted later |
| AI credits exhausted | The flow fails visibly |

## 12. Phasing

| Phase | What | Exit |
|---|---|---|
| 1 (2 to 4 weeks) | Flow and script run; I promote by hand; every catch and near miss is logged | No leaks into `_Intake` for two consecutive weeks (proposed) and a false-positive rate I find workable |
| 2 | Automatic promotion for Lane A | |
| 3 | Add B1 and B4 | |
| Later | Lane B, Lane C, calendar, draft-reply loop, Studio agent | Each needs its own addendum |

## 13. Acceptance

- Synthetic fixtures (clean, leaky, borderline) with a passing validator test suite.
- A flow dry run on synthetic mail in a test folder.

## 14. Open items

- Developer environment `nwgms-digest-dev`: AI credits and whether "Run a prompt" works.
- Scheduled prompt output route for B1.
- Whether Genial `#` numbers can exceed 6 digits (the pattern allows 4 to 6).
- Only Patient # appears in my inbox. The other identifiers in section 7 stay blocked as a precaution and can be relaxed on evidence.
- Lane C method.
- Teams recap policy and MFT policy lookups.
- `CLAUDE.md` section 6 in the vault still lists IG/DPO sign-off as outstanding. This spec makes no claim that it has been obtained.

## 15. Decisions so far

- Option 2 first; Option 3 pieces one at a time afterwards.
- Lane A is the ACTION folder only. WAITING is not used.
- Subject lines and free-text gist are dropped in v1.
- Scheduled flow, not a new-email trigger.
- Phase 1 promotion into `_Intake` is manual.
- Repo holds the canonical spec; the vault holds a pointer note.
