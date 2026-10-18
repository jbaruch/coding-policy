## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls `poll-pr-reviews.sh` at a script-owned interval up to a script-owned budget and watches exactly the fields the Step 7 merge gate reads — each gating bot's latest review state (resolved by bot login), CI status, and merge state. Do not hand-roll a poll loop, and do not wrap the watch in an invented wall-clock `timeout` (see `rules/ci-safety.md` "Always Watch CI"). Read the release contract's Pre-Merge Watch Mechanics first; it binds this step:

```text
skills/release/references/release-contract.md
```

Then run the watcher:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/release/watch-pr-reviews.sh" <owner> <repo> <pr-number>
```

It returns the full `poll-pr-reviews.sh` snapshot plus a `watch` object — `{"result": ..., "attempts": N, "elapsed_seconds": N}`. The interval/budget constants and the result contract are the script's, not restated here (`rules/script-as-black-box.md` — see the header's result matrix). Branch on `.watch.result`:

- `ready` (exit 0) — mergeable, CI `success`/`none`, both bots posted, no Copilot review still owed, the policy reviewer not `CHANGES_REQUESTED` (Copilot may be, and this still reaches ready — it is always advisory). Read every `review_bodies[].body`, from every author and page, then proceed to Step 6.
- `changes_requested` (exit 0) — the policy reviewer requested changes (a blocking finding). Go to Step 6, address it, push; the next push re-fires the review, so re-run the watcher.
- `ci_failure` (exit 0) — a check failed. Fix it (Step 6), push, re-run the watcher.
- `dirty` (exit 0) — the branch conflicts with `main` and GitHub skipped the `pull_request:` workflows. Rebase onto current `main`, resolve, force-push, then re-run the watcher — the push re-fires the missed workflows.
- `review_unrequested` (exit 1) — Copilot has no verdict at this head, no pending request and no run in flight, so no wait can produce one. Request it with Step 4's command block and re-run the watcher. The policy reviewer runs on the push and never produces this result.
- `pending_at_budget` (exit 1) — a signal never arrived within the budget (a reviewer that never posted, CI stuck pending). Inspect which field is still `none`/`pending` in the returned snapshot. If the policy reviewer never posted on coding-policy's own PRs, check the `review-codex.yml` run (`gh run list --workflow review-codex.yml`) — a missing or expired `CODEX_AUTH_JSON` secret is the usual cause. On a consumer repo, confirm `review-trigger.yml` dispatched and the fleet App ran in coding-policy; its scheduled poll is the backstop. Re-run the watcher to keep waiting once the cause is understood.
