---
alwaysApply: true
---

# Boy Scout Rule

## The Principle

- Leave the authorized task surface in better shape than you found it
- An observation authorizes nothing beyond How to Apply
- Fix a defect when it is coherent with the task's accepted behavior and authorized scope

## How to Apply

- **In-scope defect** — fix it in the current change and test the outcome
- **Out-of-scope finding** — open one issue in an operator-owned repo for a defect or binding policy mismatch under `rules/review-severity.md` Categories and Evidence when no open issue covers its root cause
- Record optional hardening, presentation, unproven harm or a tracked root cause once in the task record or PR body, never an issue
- **Unrelated blocking risk** — record it once with evidence, consequence, owner if known, and required next decision: in the issue an out-of-scope finding files, else the existing authorized task record or current conversation
- **Advisory** — use `rules/review-severity.md` Split Reading From Acting
- Maintenance due in another repository and unrelated checkout hygiene stay observations and never gate the selected task
- `rules/external-repo-contributions.md` governs repositories the operator does not own

## Reconciliation With `commit-conventions`

- Keep changes focused under `rules/commit-conventions.md`, never bundling an unrelated repair
