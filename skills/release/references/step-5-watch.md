## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls `poll-pr-reviews.sh` at a script-owned interval up to a script-owned budget and watches exactly the fields the Step 7 merge gate reads — each gating bot's latest review state (resolved by bot login), CI status, and merge state. Do not hand-roll a poll loop, and do not wrap the watch in an invented wall-clock `timeout` (see `rules/ci-safety.md` "Always Watch CI"). Read the release contract's Pre-Merge Watch Mechanics first; it binds this step:

```text
skills/release/references/release-contract.md
```

The watcher requires Python 3 on `PATH` for the review selector shipped alongside
the release helpers. Install Python 3 if it is missing or reinstall the plugin
if the selector is missing. After an install, update, or rollback, verify the
selected plugin root against the intended release before using its verdict.
For ACR, confirm the locked source revision and a clean `acr check` for the
selected agent. Recover interrupted writes with `acr realize` and recheck.
For Tessl, compare the installed inventory with the delivered package and
reinstall if incomplete. Restart the watcher from the verified root.

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/watch-pr-reviews.sh" <owner> <repo> <pr-number>
```

It returns the full `poll-pr-reviews.sh` snapshot plus a `watch` object — `{"result": ..., "attempts": N, "elapsed_seconds": N}`. The result meanings and readiness predicate belong to `skills/release/watch-pr-reviews.sh` — its top-of-file result matrix. The actions below consume those results without duplicating their conditions. Branch on `.watch.result`:

- `ready` (exit 0) — the script reports merge readiness. Proceed to Step 6 to read every review body and inline comment before acting.
- `changes_requested` (exit 0) — go to Step 6, address blocking findings from either reviewer, push and re-run the watcher.
- `ci_failure` (exit 0) — fix the failed checks through Step 6, push and re-run the watcher.
- `dirty` (exit 0) — rebase onto current `main`, resolve conflicts, force-push the owned feature branch and re-run the watcher.
- `pending_at_budget` (exit 1) — inspect which field is still `none`/`pending` in the returned snapshot.
  For either review lane with `state: none`, inspect retained `submitted_at`, `commit_id`, and `body`.
  Open the matching review's `html_url` from `review_bodies` if available. Compare the native
  `commit_id` with the snapshot's `head_sha`. If the quality review body records a `Reviewed commit`,
  compare it with both SHAs. A missing, ambiguous, or dismissed review leaves the gate pending, as
  does a SHA mismatch or a missing or ambiguous `Reviewed commit` in a quality review.

  For each pending review lane, follow the ordinary current-head trigger or failure recovery in
  [Step 4](step-4-review.md). Check the lane's workflow if there is no submission evidence.
  Re-run the watcher.
