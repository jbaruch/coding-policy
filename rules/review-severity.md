---
alwaysApply: true
---

# Review Severity

## Categories and Evidence

- Prefix messages with `[defect]`, `[policy mismatch]` or `[optional hardening]`.
- Defects require a concrete trigger, reachable path and material consequence.
- Policy mismatches cite violated binding clauses and source; identify unproven harm as unproven.
- Optional hardening adds safeguards without a demonstrated defect or obligation.

## Severity and Remedy

- Blocking: substantiated material defects, tests concealing them, or binding obligations affecting meaning or behavior.
- Advisory: optional hardening, prose, naming and presentation.
- A behavior-changing suggestion alone never establishes a blocker.
- Resolve mismatches through compliant code or authorized policy amendment and re-review; reviewers cannot waive policy.
- Apply `rules/simplicity.md` to remedy cost and scope.

## Merge Gate

- Any blocker → `CHANGES_REQUESTED`; otherwise `APPROVED` or `COMMENTED`.
- Retain both independent review gates.

## Judge-Weighed Finding Carve-Out

- Narrow exception for merging with a blocking policy finding a weighing ruled `defer` or `decline`.
- Applies only when fixing costs more than the failure it prevents.
- Preconditions are binding: read `skills/release/references/review-severity-carve-outs.md` Judge-Weighed Finding Carve-Out before relying on it.
- Every other blocker is resolved before merge.

## Reading and Acting

- Read all feedback under `rules/reviewer-feedback-reading.md`.
- Advisory → note it directly in the existing review conversation or authorized task record.
- Fold advisories into required same-surface fixes only without another push or check round.
- Advisories require no push, review round, issue, PR or delivery prerequisite.
