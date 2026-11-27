## Step 6 — Address Feedback

- Follow `rules/reviewer-feedback-reading.md` using Step 5's `review_bodies[].body` and `inline_comment_bodies[].body` fields. The bot-specific `reviews` fields route verdicts only.
- Follow `rules/review-severity.md` for evidence, classification and disposition. Use the disputed policy blocker flow below when it applies.
- Fix CI failures.
- **Reply to addressed blocking findings.** Ordinary advisory replies may accompany the existing review work, but are outside merge prerequisites. Use these opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
  - Advisory: `Acknowledged — advisory noted`. Never create a new follow-up obligation
- **Disputed policy blocker.** Post a PR conversation comment in the `Declining —` form above. Link the review and cite concrete evidence for the disputed finding.
  - If there is no source correction to push, start from the current PR head on its source branch with a clean index. Create one intentional empty commit, for example `git commit --allow-empty -m "chore(review): Trigger fresh policy and quality reviews"`, and push it normally to that branch.
  - If the fresh policy review still blocks on the finding, keep the merge blocked and resolve the finding under `rules/review-severity.md` before another review round.

- **Legacy identity gate after App approval:** follow Step 7's migration cleanup, then return to Step 5. A legacy change request alone does not require another fix push after the current policy App approves.

- Push corrections normally to the same PR source branch. After each push, return immediately to Step 5. The [existing `synchronize` routes](REVIEW_DETAILS.md#policy-reviewer--two-deployments) run both reviewers on the new commit.
- Proceed immediately to Step 7 when Step 5 returns `ready`, both independent reviewers have posted nonblocking verdicts on the exact current head (`APPROVED` or `COMMENTED` with only advisories), required CI for that head passes, and addressed blocking findings have replies.
