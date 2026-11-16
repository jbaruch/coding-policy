---
alwaysApply: true
---

# Boy Scout Rule

## The Principle

- Leave the authorized task surface in better shape than you found it
- An observation authorizes nothing beyond How to Apply
- Maintenance due in another repository and unrelated checkout hygiene stay observations, never task prerequisites

## How to Apply

- **In-scope defect** — fix a defect coherent with the task's accepted behavior and authorized scope in the current change and test the outcome
- **Out-of-scope finding** — open one issue in an operator-owned repo for a defect or binding policy mismatch under `rules/review-severity.md` Categories and Evidence when no open issue covers its root cause
- Record optional hardening, presentation, unproven harm or a tracked root cause once in the task record or PR body, never an issue
- **Unrelated blocking risk** — record it once with evidence, consequence, owner if known, and required next decision: in the existing authorized task record or current conversation
- **Advisory** — use `rules/review-severity.md` Split Reading From Acting
- `rules/external-repo-contributions.md` governs every other repository

## Reconciliation With `commit-conventions`

- Keep changes focused under `rules/commit-conventions.md`, never bundling an unrelated repair
