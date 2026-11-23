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
- For a disputed policy blocker, reply with the evidence and keep the gate open until the normal policy re-review clears it.

- **Legacy identity gate after App approval:** follow Step 7's migration cleanup, then return to Step 5. A legacy change-request state alone does not require another fix push after the current policy App approved; every body still requires reading.

- Push fixes to the same branch
- Both reviewers run automatically after each required push; return to Step 5.
- The policy reviewer re-runs automatically on every push (coding-policy via `review-codex.yml` `pull_request: synchronize`; consumers via `review-trigger.yml` re-dispatching the fleet App). No manual re-request.
- Repeat Step 5 until neither reviewer carries a blocking finding — `APPROVED` or `COMMENTED` with its body read and only advisories — with addressed blocking findings replied to. Proceed immediately to Step 7.
