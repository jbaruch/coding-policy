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
- **Unrelated blocking risk** — record it once in the task report with evidence, consequence, owner if known, and required next decision
- **Advisory** — acknowledge it in the task report or round log; it creates no delivery obligation
- Open an issue, pull request, or other external record only when the task or operator separately authorizes that action

## Fold Into a Round Already in Flight

- Fold an adjacent advisory only when an already-required correction touches the same surface and the advisory adds no push or verification round
- Never spin up a round, push, issue, or pull request solely for an advisory
- Maintenance due in another repository and unrelated checkout hygiene stay observations; they never become prerequisites for the selected task
- The review-pipeline form is `rules/review-severity.md`

## Reconciliation With `commit-conventions`

- `rules/commit-conventions.md` keeps each commit and pull request focused
- Record an unrelated risk instead of bundling its repair into the current change
