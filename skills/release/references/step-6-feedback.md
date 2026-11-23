## Step 6 — Address Feedback

- **Read every review in full first.** Read every `review_bodies[].body` and `inline_comment_bodies[].body` in Step 5's snapshot, including human, other-bot and historical reviews, before judging any item (see `rules/reviewer-feedback-reading.md`). The bot-specific `reviews` fields route verdicts, never limit reading
- **Classify before acting** under `rules/review-severity.md`: distinguish demonstrated defects, binding policy mismatches and optional hardening. Changing behavior alone does not make a correction mandatory.
- **Resolve blocking findings** with the smallest sufficient correction. For policy mismatches, consider an authorized policy amendment alongside a code change; keep the current obligation and review gate effective until the amendment is reviewed and installed.
- **Advisory findings**: note them directly in review replies.
- Fold an advisory only when an already-required blocking correction touches the same surface and adds no push or verification round
- An advisory creates no obligation for an issue, follow-up, push or delivery prerequisite
- Never run an advisory-only re-review round
- **CI failures**: Fix every one
- **Review suggestions**: Apply what's right. Push back on anything that misreads scope — cite concrete evidence (file:line, log line, spec quote) when declining
- **Reply to addressed blocking findings.** Ordinary advisory replies may accompany the existing review work, but are outside merge prerequisites. Use these opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
  - Advisory: `Acknowledged — advisory noted`. Never create a new follow-up obligation
- **Disputed policy blocker.** Post a PR conversation comment in the `Declining —` form above. Link the review and cite concrete evidence for the disputed finding.
  - If there is no source correction to push, start from the current PR head on its source branch with a clean index. Create one intentional empty commit, for example `git commit --allow-empty -m "chore(review): Trigger fresh policy and quality reviews"`, and push it normally to that branch.
  - If the fresh policy review still blocks on the finding, keep the merge blocked and resolve the finding under `rules/review-severity.md` before another review round.

- **Legacy identity gate after App approval:** follow Step 7's migration cleanup, then return to Step 5. A legacy change-request state alone does not require another fix push after the current policy App approved; every body still requires reading.

- Push corrections normally to the same PR source branch. After each push, return immediately to Step 5. The [existing `synchronize` routes](REVIEW_DETAILS.md#policy-reviewer--two-deployments) run both reviewers on the new commit.
- Proceed immediately to Step 7 when Step 5 returns `ready`, both reviewers have posted nonblocking verdicts on the current commit (`APPROVED` or `COMMENTED` with only advisories), required CI passes, and addressed blocking findings have replies.
