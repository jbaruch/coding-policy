---
alwaysApply: true
---

# Review Severity

## Categories and Evidence

- Prefix with `[defect]`, `[policy mismatch]` or `[optional hardening]`.
- Defects require a concrete trigger, reachable path and material consequence.
- Policy mismatches cite binding clauses and source.
- Label unproven harm.
- Optional hardening lacks a demonstrated defect or obligation.
- Time-scoped obligations bind within their stated window only.
- An expired exception restores its ordinary rule.

## Severity and Remedy

- Blocking: substantiated material defects, tests concealing them, or binding obligations affecting meaning or behavior.
- Advisory: optional hardening and presentation-only suggestions.
- Behavior change alone proves no blocker.
- Re-review blocks only the last review's unresolved blockers, plus defects and binding policy mismatches in subsequently changed lines, with their regressions.
- Other findings present at the last reviewed head are advisory on re-review.
- An unreadable last reviewed head requires a first review.
- Resolve mismatches through compliance or authorized amendment and re-review.
- Reviewers cannot waive policy.
- Apply `rules/simplicity.md` to remedy cost and scope.

## Merge Gate

- Any blocker → `CHANGES_REQUESTED`; otherwise `APPROVED` or `COMMENTED`.
- Retain both independent review gates.

## Judge-Weighed Finding Carve-Out

- Narrow exception for merging with a blocking policy finding ruled `defer` or `decline`.
- Applies to wrong findings or fixes costing more than the failure prevented.
- Preconditions are binding: read `skills/release/references/review-severity-carve-outs.md` Judge-Weighed Finding Carve-Out before relying on it.
- Every other blocker is resolved before merge.

## Split Reading From Acting

- Read all feedback under `rules/reviewer-feedback-reading.md`.
- Advisory → note it directly in the existing review conversation or authorized task record.
- Fold advisories into required same-surface fixes only without another push or check round.
- Advisories require no push, review round, issue, PR or delivery prerequisite.
