---
alwaysApply: true
---

# Boy Scout Rule

## The Principle

- Leave the authorized task surface in better shape than you found it
- An observation grants no authority to expand the task, write another repository, or create external follow-up work
- Fix a defect when it is coherent with the task's accepted behavior and authorized scope

## How to Apply

- **In-scope defect** — fix it in the current change and test the outcome
- **Unrelated blocking risk** — record it once with evidence, consequence, owner if known, and required next decision: in the existing task/round record in a Herdr round, or the current conversation standalone
- **Advisory** — use `rules/review-severity.md` Split Reading From Acting
- Open an issue, pull request, or other external record only when the task or operator separately authorizes that action

## Fold Into a Round Already in Flight

- Advisory folding follows `rules/review-severity.md` Split Reading From Acting
- Maintenance due in another repository and unrelated checkout hygiene stay observations
- These observations never become prerequisites for the selected task

## Reconciliation With `commit-conventions`

- `rules/commit-conventions.md` keeps each commit and pull request focused
- Record an unrelated risk instead of bundling its repair into the current change
