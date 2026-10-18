## Step 6 — Address Feedback

- **Read every review in full first.** Read every `review_bodies[].body` and `inline_comment_bodies[].body` in Step 5's snapshot, including human, other-bot and historical reviews, before judging any item (see `rules/reviewer-feedback-reading.md`). The bot-specific `reviews` fields route verdicts, never limit reading
- **Then act by severity** (see `rules/review-severity.md`): blocking findings — fix now; advisory findings — acknowledge in the existing team task report or round log, or note them directly in standalone review replies.
- Fold an advisory only when an already-required blocking correction touches the same surface and adds no push or verification round
- An advisory creates no obligation for an issue, follow-up, push or delivery prerequisite
- Never run an advisory-only re-review round
- **CI failures**: Fix every one
- **Review suggestions**: Apply what's right. Push back on anything that misreads scope — cite concrete evidence (file:line, log line, spec quote) when declining
- **Reply to addressed blocking findings.** Ordinary advisory replies may accompany the existing review work, but are outside merge prerequisites. Use these opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
  - Advisory: standalone, `Acknowledged — advisory noted`; in a team round, `Acknowledged — recorded in <task report or round-log ref>`. Use the existing record; never simulate a team report or create a new follow-up obligation
  - Judge-weighed deferral keeps `Acknowledged — deferred to #<follow-up issue> (judge ruling <digest>)` under `skills/release/references/REVIEW_DETAILS.md`; its ruling and issue obligations are unchanged
- **Marginal blocking finding:** a nominated finding may go to a weighing instead of a fix (`rules/review-severity.md` Judge-Weighed Finding Carve-Out)
  - Standalone, the operator is the judge
  - Standalone, nominate a finding only when it sits on lines the previous fix push added, or with a cited reachability claim marking it marginal
  - Standalone, ask one decision question per gate, carrying every nomination
  - Standalone, keep fixing while the question is open
  - Standalone, treat no answer as `fix`
  - In a Herdr team round the worker nominates nothing and asks no question
  - In a team round, report the blocking findings to the foreman, which nominates and has the pinned judge weigh them
  - In a team round, the release brief then names the judge's report as the ruling file
  - The question template, ruling file and reply literals are in:

  ```text
  skills/release/references/REVIEW_DETAILS.md
  ```

- **Ruled policy review:** list or dismiss with the script below
  - The follow-up issue is in the PR's own repository; the script takes an issue number on that repository
  - Standalone, run the first invocation below
  - In a Herdr team round, run the second, with the judge's report and the `--task` the release brief names
  - In a repository the operator does not own, ask first per `rules/external-repo-contributions.md`
  - Without `--ruling` it lists the blocking findings to name in the question
  - With `--ruling` it posts the ruled findings to the follow-up issue, then dismisses the review, only when its predicate holds
  - Exit 0 dismissed, noop or listed; 1 predicate unmet, `.unmet` naming each failed condition; 2 usage or API error
  - The predicate is the script's — see `skills/release/dismiss-ruled-review.sh` header, not restated here (`rules/script-as-black-box.md`)

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  bash "$CP/skills/release/dismiss-ruled-review.sh" <owner> <repo> <pr-number> --ruling <ruling-file> --followup-issue <issue-number>
  ```

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  bash "$CP/skills/release/dismiss-ruled-review.sh" <owner> <repo> <pr-number> --ruling <judge-report> --followup-issue <issue-number> --task <task-id>
  ```

- Push fixes to the same branch
- **Re-request Copilot after every push** with Step 4's command block. Copilot does not re-post on its own.
- The policy reviewer re-runs automatically on every push (coding-policy via `review-codex.yml` `pull_request: synchronize`; consumers via `review-trigger.yml` re-dispatching the fleet App). No manual re-request.
- Repeat Step 5 until the policy reviewer carries no blocking finding — `APPROVED`, `COMMENTED` with its body read and only advisories, or `RULED` after a ruled dismissal — with addressed blocking findings replied to. Proceed immediately to Step 7.

