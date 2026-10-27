# ADR 0002: Content lane via a local deterministic stripper

Status: proposed (27 Oct 2026)
Supersedes: nothing. Lane A (AI-extraction digest) is retained as a fallback; go-live paused.

## Context
Lane A allowlists fields out of Copilot summaries, so it can never carry message content.
Fraser wants a content-bearing lane. Raw mail cannot enter the vault or reach Claude, so
content must be stripped before it crosses the trust boundary. Design: vault
Projects/Comms-Intake-Design.md v0.3. Tests: Projects/Comms-Test-Plan.md.

## Decision
Build a local Python stripper in this repo (stripper/) on a content-lane branch.
- Deterministic detectors (reusing validator/patterns.py), local NER (Presidio + spaCy),
  a name gate, a clinical lexicon, and a fail-closed residual rescan.
- No AI and no network calls in the stripping path.
- Patient identifiers: fixed placeholders, no map. Referral/sample numbers: REF-/SMP-
  keyed-hash codes, reversible locally only. Whole-message quarantine, never partial output.
- Trust boundary: the stripper runs on Fraser's work laptop; stripped output written to the
  vault is the point where content leaves the Trust-controlled zones. Z2/Z3/Z4 are never
  reachable by Claude.
- Test evidence is synthetic only (labelled corpus, per-category recall, counts only).
- validator/ is unchanged. The stripper imports its patterns; it does not fork them.

## Consequences
- Repo remains Tier 0/1: generated corpora are reproducible from a seed and git-ignored.
- Stripper correctness becomes the main control, so every change reruns the full suite.
- Residual risk: recall below 100% on free-text names and clinical wording. Mitigated by
  the rescan and quarantine, and measured by the recall report.
- No IG sign-off is claimed. Informal conversations with Liz Jones and the Quality team
  are pending.

## Alternatives considered
- Lane A only: safe but metadata-only.
- Manual redaction: does not scale; the vault forbids it outside this lane.
- Cloud PII services: network calls and a new processor, which fails the no-network principle.
