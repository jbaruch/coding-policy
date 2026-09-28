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
- Copilot findings are always advisory regardless of Copilot's review state — even a Copilot `CHANGES_REQUESTED` never gates the agent's flow
- Anything whose fix changes only presentation, not behavior

## Gating Predicate

- Any blocking finding present → the reviewer posts `CHANGES_REQUESTED` and the merge gates
- Only advisory findings → the reviewer posts `COMMENTED` and the merge is allowed
- The policy reviewer's posted state already encodes this — the event is derived from per-finding severity (see `.github/codex-review/post-review.sh` header)
- The merge watcher gates on the policy reviewer's `CHANGES_REQUESTED` alone (see `skills/release/watch-pr-reviews.sh` header)
- Copilot never gates

## Judge-Accepted Defect Carve-Out

- Narrow exception for shipping with a blocking finding still open
- Applies when a fix loop did not converge and the pinned judge's diagnosis answers it with `REMEDY: stop`
- Preconditions (all required):
  1. The task's current exhaustion carries a recorded `stop` diagnosis under `rules/agent-team-operation.md` Judge Seat
  2. The remaining blocking finding is recorded as a tracked accepted defect with its issue reference
  3. The shipped scope excludes that defect's work, and what ships carries no other blocking finding
  4. Every other release gate holds: CI green, the external reviews, and independent reviewer and tester passes on the shipped tip
- The operator overrides a `stop` like any ruling, by authorizing a plan over it
- Every other blocking finding is fixed before merge

## Judge-Weighed Finding Carve-Out

- Narrow exception for merging with a blocking finding a weighing ruled `defer` or `decline`
- Applies when fixing the finding costs more than the failure it prevents
- A weighing asks whether fixing a finding is worth its cost, never whether the finding is real
- A finding is nominated only when it sits on lines the previous fix push added, or a report marks it `MARGINAL:` with a cited reachability claim
- One weighing carries every nomination for one gate
- The ruling is a file in the format `skills/release/dismiss-ruled-review.sh` header names, first line `RULING: weighed`
- In a Herdr team round the pinned judge writes the ruling (see `rules/agent-team-operation.md` Judge Seat)
- In standalone mode the operator is the judge
- The agent asks the operator one decision question per gate, naming every nominated finding
- The question never blocks the round
- The agent keeps fixing until the answer arrives
- No answer means `fix`
- The agent records the answer verbatim as the ruling file
- Preconditions (all required):
  1. A completed weighing ruling rules the finding `defer` or `decline`
  2. In standalone mode, the ruling quotes the operator's answer verbatim
  3. The ruling names the finding at its `HEAD:`, and the finding's file is unchanged from that commit to the head
  4. The finding is under no floor
  5. A `defer` finding is entered in the task's follow-up issue, citing the ruling
  6. Every other release gate holds
- Floors, each ruled `fix`:
  - a failing test, lint, diagnostic or required check
  - a security or data-loss defect reachable by normal inputs
  - a `no-secrets` or `ci-safety` finding
  - a carve-out's unmet precondition
  - an unmet acceptance criterion the operator stated
- A ruling never skips, disables or removes a test
- A gating policy review the ruling covers is dismissed per `rules/ci-safety.md` Judge-Ruled-Review Dismissal Carve-Out
- Every other blocking finding is fixed before merge

## Split Reading From Acting

- Read every finding in full first — severity never licenses skipping a body (see `rules/reviewer-feedback-reading.md`)
- Blocking → fix before merge
- Advisory → acknowledge
- Fold an advisory in only when a blocking round is already happening
- Otherwise defer the advisory to a follow-up PR or issue and reference it from the current PR
- Never burn a dedicated re-review round on a lone advisory
