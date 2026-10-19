## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls `poll-pr-reviews.sh` at a script-owned interval up to a script-owned budget and watches exactly the fields the Step 7 merge gate reads — each gating bot's latest review state (resolved by bot login), CI status, and merge state. Do not hand-roll a poll loop, and do not wrap the watch in an invented wall-clock `timeout` (see `rules/ci-safety.md` "Always Watch CI"). Read the release contract's Pre-Merge Watch Mechanics first; it binds this step:

```text
skills/release/references/release-contract.md
```

Then run the watcher:

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/watch-pr-reviews.sh" <owner> <repo> <pr-number>
```

It returns the full `poll-pr-reviews.sh` snapshot plus a `watch` object — `{"result": ..., "attempts": N, "elapsed_seconds": N}`. The result meanings and readiness predicate belong to `skills/release/watch-pr-reviews.sh` — its top-of-file result matrix. The actions below consume those results without duplicating their conditions. Branch on `.watch.result`:

- `ready` (exit 0) — the script reports merge readiness. Proceed to Step 6 to read every review body and inline comment before acting.
- `changes_requested` (exit 0) — go to Step 6, address the blocking policy findings, push and re-run the watcher.
- `ci_failure` (exit 0) — fix the failed checks through Step 6, push and re-run the watcher.
- `dirty` (exit 0) — rebase onto current `main`, resolve conflicts, force-push the owned feature branch and re-run the watcher.
- `review_unrequested` (exit 1) — request Copilot with Step 4's command block and re-run the watcher.
- `pending_at_budget` (exit 1) — a signal never arrived within the budget (a reviewer that never posted, CI stuck pending). Inspect which field is still `none`/`pending` in the returned snapshot. If the policy reviewer never posted on coding-policy's own PRs, check the `review-codex.yml` run (`gh run list --workflow review-codex.yml`) — a missing or expired `CODEX_AUTH_JSON` secret is the usual cause. On a consumer repo, confirm `review-trigger.yml` dispatched and the fleet App ran in coding-policy; its scheduled poll is the backstop. Re-run the watcher to keep waiting once the cause is understood.
