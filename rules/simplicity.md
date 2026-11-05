---
alwaysApply: true
---

# Simplicity

## Before Implementation

- State the problem and smallest sufficient solution in the task conversation or PR.
- Before adding automation, check whether a scoped instruction and existing agent actions solve the problem. Prefer platform behavior, direct operations and deletion.
- Treat an issue’s proposed mechanism as a suggestion; explain why the simpler option is insufficient.
- Justify custom state, recovery, coordination and abstractions with a requirement and platform gap.
- Weigh maintenance, rollout and failure paths against likelihood and impact.
- Future consumers do not justify machinery.
- Keep explanations brief; add no approval gate.

## During Review

- Apply `rules/review-severity.md`.
- Recommend the smallest sufficient remedy.
- Consider removal or authorized policy narrowing before extra safeguards.
- Keep policy binding until an authorized amendment takes effect.

## Calibration

- Before model comparisons, read `skills/coding-contracts/references/review-calibration.md` in full.
- Match role, instructions and source before attributing results to models.
- Calibration is on demand, never a merge prerequisite.
