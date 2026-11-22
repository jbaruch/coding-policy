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
  - Judge-weighed deferral keeps `Acknowledged — deferred to #<follow-up issue> (judge ruling <digest>)` under `skills/release/references/REVIEW_DETAILS.md`
- **Marginal blocking policy finding:** a nominated policy finding may go to a weighing instead of a fix (`rules/review-severity.md` Judge-Weighed Finding Carve-Out)
  - The judge is the operator, or a software-factory judge
  - A software-factory judge's ruling file is its collected `<task>.<id>.<harness>.report.txt`, emitted whole in the ruling format
  - Cite that report by absolute path and sha256; never edit it
  - A software-factory judge rules only `decline` or `defer`
  - A judge `decline` shows the finding misreads the code or the governing policy
  - A judge `defer` shows the finding pre-existing or outside the PR's goal, with a follow-up issue and a cost comparison
  - Only the operator rules risk acceptance or a rule waiver
  - Nominate a finding only when it sits on lines the previous fix push added, or with a cited reachability claim marking it marginal
  - A gate with any nomination no judge ruling covers goes wholly to the operator
  - Ask the operator one decision question per gate, carrying every nomination
  - Keep fixing while the question is open
  - Treat no answer as `fix`
  - The question template, judge brief, ruling file and reply literals are in:

  ```text
  skills/release/references/REVIEW_DETAILS.md
  ```

- **Ruled policy review:** list or dismiss with the script below
  - The follow-up issue is in the PR's own repository; the script takes an issue number on that repository
  - Run the invocation below
  - In a repository the operator does not own, ask first per `rules/external-repo-contributions.md`
  - Without `--ruling` it lists the blocking findings to name in the question
  - Pass `--ruling-sha256` with a judge's report
  - With `--ruling` it posts the ruled findings to the follow-up issue, then dismisses the review, only when its predicate holds
  - Exit 0 dismissed, noop or listed; 1 predicate unmet, `.unmet` naming each failed condition; 2 usage or API error
  - The predicate is the script's — see `skills/release/dismiss-ruled-review.sh` header, not restated here (`rules/script-as-black-box.md`)

  ```bash
  KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  bash "$KS/dismiss-ruled-review.sh" <owner> <repo> <pr-number> --ruling <ruling-file> [--ruling-sha256 <sha256>] --followup-issue <issue-number>
  ```

- **Legacy identity gate after App approval:** follow Step 7's migration cleanup, then return to Step 5. A legacy change-request state alone does not require another fix push after the current policy App approved; every body still requires reading.

- Push fixes to the same branch
- Both reviewers run automatically after each required push; return to Step 5.
- The policy reviewer re-runs automatically on every push (coding-policy via `review-codex.yml` `pull_request: synchronize`; consumers via `review-trigger.yml` re-dispatching the fleet App). No manual re-request.
- Repeat Step 5 until neither reviewer carries a blocking finding — `APPROVED`, `COMMENTED` with its body read and only advisories, or a policy `RULED` after a ruled dismissal — with addressed blocking findings replied to. Proceed immediately to Step 7.
