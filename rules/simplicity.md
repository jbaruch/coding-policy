---
alwaysApply: true
---

# Simplicity

## KISS and YAGNI

- Choose the simplest design that solves the stated problem today.
- Build nothing for a need nobody has today.
- Overengineering is forbidden.
- Add no option, flag, schema version, fallback, abstraction or extension point without a current caller or an observed failure.
- One caller gets no abstraction.
- One value gets no setting.
- Future consumers justify no machinery.
- Prefer deleting code, state and process over adding them.
- Delete code, options and files a change leaves unused in that change.

## Before Implementation

- State problem and smallest sufficient solution in conversation or PR.
- Try scoped instructions, agent actions, platform behavior or deletion before automation.
- Treat issue proposals as suggestions; explain simpler options' gaps before adopting machinery.
- Justify custom state, recovery, coordination and abstractions with requirements and platform gaps.
- Weigh maintenance, rollout and failures against likelihood and impact.
- Explain briefly.
- Add no approval gate.

## During Review

- Apply `rules/review-severity.md`.
- Flag machinery added without the justification above as a `[policy mismatch]` against this rule.
- Recommend smallest sufficient remedy.
- Consider deletion or authorized policy narrowing before safeguards.
- Keep policy binding until an authorized amendment takes effect.

## Calibration

- Before model comparisons, read `skills/coding-contracts/references/review-calibration.md` in full.
- Calibrate on demand, never as a merge gate.
