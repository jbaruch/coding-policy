---
alwaysApply: true
description: Review findings carry a severity — blocking gates the merge, advisory never does; read every finding, act by severity.
---

# Review Severity

## Two Tiers

- Every review finding is **blocking** or **advisory**
- The test is behavioral — does fixing the finding change what an agent or the pipeline does?
- Blocking: the fix changes behavior or closes a contract gap
- Advisory: the fix changes only presentation

## Blocking — Gates the Merge

- Correctness and security defects
- Policy-contract violations: a carve-out's unmet preconditions, `no-secrets`, `ci-safety` gate-evasion, surface-sync that breaks publish
- A rule directive whose violation changes agent behavior
- A style finding whose fix changes meaning — an atomic-bullet split that alters what the bullet directs

## Advisory — Never Gates

- Pure prose and style: `context-writing-style` connective or em-dash placement, a presentation-only atomic-bullet split
- CHANGELOG wording, naming taste, synonym preference
- Copilot findings are always advisory — Copilot posts `COMMENTED` and never gates the agent's flow
- Anything whose fix changes only presentation, not behavior

## Gating Predicate

- Any blocking finding present → the reviewer posts `CHANGES_REQUESTED` and the merge gates
- Only advisory findings → the reviewer posts `COMMENTED` and the merge is allowed
- The policy reviewer's posted state already encodes this — the event is derived from per-finding severity (see `.github/codex-review/post-review.sh` header)
- The merge watcher gates on the policy reviewer's `CHANGES_REQUESTED` alone; Copilot never gates (see `skills/release/watch-pr-reviews.sh` header)

## Split Reading From Acting

- Read every finding in full first — severity never licenses skipping a body (see `rules/reviewer-feedback-reading.md`)
- Blocking → fix before merge
- Advisory → acknowledge; fold in only when a blocking round is already happening, else defer to a follow-up (see `rules/boy-scout.md`)
- Never burn a dedicated re-review round on a lone advisory
