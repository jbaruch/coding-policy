---
alwaysApply: true
---

# Review Severity

## Categories and Evidence

- Prefix messages with `[defect]`, `[policy mismatch]` or `[optional hardening]`.
- Defects require a concrete trigger, reachable path and material consequence.
- Policy mismatches cite binding clauses and source.
- For a blocker about a claimed state, cite a current supported path or show a violation of an independently binding explicit contract such as an invariant.
- Name the producer or transition when citing reachability, such as a writer, migration, import, restore, rollback or documented failure sequence.
- Include relevant production concurrency and untrusted input boundaries.
- Do not treat an impossible fixture or hypothetical future writer as evidence of a current defect.
- Identify unproven harm as unproven.
- Optional hardening adds safeguards without a demonstrated defect or obligation.

## Severity and Remedy

- Blocking: substantiated material defects, tests concealing them, or binding obligations affecting meaning or behavior.
- Advisory: optional hardening and presentation-only suggestions.
- A behavior-changing suggestion alone never establishes a blocker.
- On a re-review, only unresolved blockers of the last review, plus defects and binding policy mismatches in lines changed since it, with their regressions, block.
- On a re-review, anything else already present at the last reviewed head is advisory.
- A re-review whose last reviewed head is unreadable counts as a first review.
- Resolve mismatches through compliant code or authorized policy amendment and re-review.
- Keep the obligation and review gate in force until any amendment is reviewed and installed.
- Reviewers cannot waive policy.
- Apply `rules/simplicity.md` to remedy cost and scope.

## Merge Gate

- Any blocker → `CHANGES_REQUESTED`; otherwise `APPROVED` or `COMMENTED`.
- Retain both independent review gates.

## Split Reading From Acting

- Read all feedback under `rules/reviewer-feedback-reading.md`.
- Independently check claimed states against current source and contracts.
- Decline or downgrade unsupported findings with concrete evidence.
- Ask for the missing supported path or binding contract instead of adding defensive branches.
- Advisory → note it directly in the existing review conversation or authorized task record.
- Fold advisories into required same-surface fixes only without another push or check round.
- Advisories require no push, review round, issue, PR or delivery prerequisite.
