---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  dual-lens automated review (the Codex code-review app for `rules/*.md`
  compliance + Copilot for correctness and risk), merge, and branch cleanup. Covers
  readiness checks, version
  reasoning, review polling, feedback handling, and post-merge verification.
  Use when the user wants to open a pull request, ship code, merge a branch,
  or handle post-merge cleanup on GitHub.
---

# Release Skill

Structured workflow for shipping code: PR creation, automated policy review, merge, and cleanup. Process each step in order — do not skip ahead, and do not stop between steps; the skill runs end-to-end from `git push` through merge + cleanup verification in a single agent session.

## Step 1 — Verify Readiness

- Confirm you're on a feature branch (not `main`/`master`)
- Run the test suite — all tests must pass
- Run the linter — no warnings or errors
- Self-audit the diff against every governing rule or skill whose domain covers the touched paths (e.g., auto-loaded prose in `rules/` or `skills/` → `rules/context-writing-style.md`; new scripts → `rules/script-delegation.md` and `rules/testing-standards.md`)
- Grep the diff for the literal markers each governing rule or skill names:
  - banned connectives `because`, `therefore`, `since`, etc. per `rules/context-writing-style.md`
  - the `outer-boundary-process-contract` token per `rules/error-handling.md`
- Run any local check the rule or skill prescribes:
  - `bash -n <script>` must exit 0 on shell scripts
  - the script's own fixture test must pass
- The Codex policy reviewer is a backstop, not the first read
- If anything fails, fix it before proceeding

## Step 2 — Create PR

- Once Step 1's readiness checks pass, create the PR automatically — the green readiness checks are the gate. Do not pause to ask a human whether to open it.
- Push the branch: `git push -u origin <branch>`
- Create the PR with `gh pr create`:
  - **Title**: `<type>(<scope>): <imperative summary>`
  - **Body**:
    ```
    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps>
    ```

When this step is wrapped in a reusable script (e.g., `release.sh` that other devs run unattended), see the script-wrapping gates at:

```text
skills/release/SCRIPTING.md
```

Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

Decide the bump per semver. Patch is the default and is handled automatically by `tesslio/patch-version-publish` — only update the manifest version for minor or major.

## Step 4 — Policy Review Fires Automatically

Opening the PR, or pushing further commits to an existing PR, automatically triggers the Codex policy reviewer — the `review-codex.yml` workflow, which runs the Codex CLI on a ChatGPT subscription and reviews the diff against the in-tree `rules/*.md`. It is bound to the `pull_request` event (`opened` / `synchronize` / `reopened`); a plain `git push` to a non-PR branch does NOT fire it, and fork PRs are skipped (no secret access). See the trigger / authorship / dismissal mechanics at:

```text
skills/release/REVIEW_DETAILS.md
```

**Also request Copilot.** Copilot is a deliberate second reviewer with a different lens — the Codex reviewer enforces `rules/*.md` compliance, Copilot reads for correctness, bugs, security, and test gaps. Both reviewers gate the merge:

```bash
skills/release/request-copilot-review.sh <owner> <repo> <pr-number>
```

Proceed immediately to Step 5.

## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls `poll-pr-reviews.sh` at a script-owned interval up to a script-owned budget and watches exactly the fields the Step 7 merge gate reads — each gating bot's latest review state (resolved by bot login), CI status, and merge state. Do not hand-roll a poll loop, and do not wrap the watch in an invented wall-clock `timeout` (see `rules/ci-safety.md` "Always Watch CI"):

```bash
skills/release/watch-pr-reviews.sh <owner> <repo> <pr-number>
```

It returns the full `poll-pr-reviews.sh` snapshot plus a `watch` object — `{"result": ..., "attempts": N, "elapsed_seconds": N}`. The interval/budget constants and the result contract are the script's, not restated here (`rules/script-as-black-box.md` — see the header's result matrix). Branch on `.watch.result`:

- `ready` (exit 0) — mergeable, CI `success`/`none`, both gating bots posted, none requested changes. Read every non-empty `reviews.*.body` (a `COMMENTED` verdict with zero inline comments still carries a body per `rules/reviewer-feedback-reading.md`), then proceed to Step 6.
- `changes_requested` (exit 0) — a gating bot requested changes. Go to Step 6, address it, push; the next push re-fires the review, so re-run the watcher.
- `ci_failure` (exit 0) — a check failed. Fix it (Step 6), push, re-run the watcher.
- `dirty` (exit 0) — the branch conflicts with `main` and GitHub skipped the `pull_request:` workflows. Rebase onto current `main`, resolve, force-push, then re-run the watcher — the push re-fires the missed workflows.
- `pending_at_budget` (exit 1) — a signal never arrived within the budget (a reviewer that never posted, CI stuck pending). Inspect which field is still `none`/`pending` in the returned snapshot. If the Codex reviewer never posted, check the `review-codex.yml` run (`gh run list --workflow review-codex.yml`) — a missing `CODEX_AUTH_JSON` secret or an expired token is the usual cause. Re-run the watcher to keep waiting once the cause is understood.

## Step 6 — Address Feedback; No Re-request Needed

- **Read every review in full first.** Read each reviewer's `reviews.*.body` and every inline comment body before judging any item — a `COMMENTED` state or zero inline comments is not a license to skip the body (see `rules/reviewer-feedback-reading.md`)
- **CI failures**: Fix every one
- **Review suggestions**: Apply what's right. Push back on anything that misreads scope — cite concrete evidence (file:line, log line, spec quote) when declining
- **Reply on EVERY thread.** Use these exact opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
- Push fixes to the same branch
- **The Codex reviewer re-runs automatically on every push** (`pull_request: synchronize`); Copilot needs a manual re-request each push via `skills/release/request-copilot-review.sh` (same args as Step 4).
- Repeat Step 5 until every active bot review is `APPROVED`, or `COMMENTED` with its body read and no blocking items, and every thread has a reply.

## Step 7 — Merge + Cleanup

Only proceed when:
- Step 5's watcher returned `.watch.result` as `ready` — its exit-0 readiness conjunction (mergeable, CI `success`/`none`, both gating bots posted, no `CHANGES_REQUESTED`); the field predicate is the watcher's, not restated here (`rules/script-as-black-box.md` — see `skills/release/watch-pr-reviews.sh` header). `ready` already requires each gating bot's `state` to have left `none`, so a reviewer that never ran cannot satisfy the gate vacuously, AND
- Every non-empty `reviews.*.body` in the returned snapshot has been read in full — a `COMMENTED` state with zero inline comments is not a license to skip the body (see `rules/reviewer-feedback-reading.md`), AND
- Every inline comment from Step 5's `inline_comments` count has a `Fixed in <sha>` or `Declining — <reason>` reply per Step 6 (verify by listing the PR's review comments — the poll script tracks counts, not reply state, so the operator confirms thread closure).

A `COMMENTED` review never gates the merge on its state alone — but its body must be read before merge, zero inline comments included. With inline comments, it is mergeable once every thread also has a reply.

Once these conditions hold, merge automatically — the green gates are the approval. Do not pause to ask a human whether to merge.

**Clear superseded review gates first.** The Codex policy reviewer posts as `github-actions[bot]`, which cannot `APPROVE` (GitHub returns HTTP 422), so a clean re-review lands as a `COMMENT` that does NOT supersede the bot's earlier `CHANGES_REQUESTED` in GitHub's merge gate — the stale request keeps `merge_state.status` at `BLOCKED`. Dismiss every such superseded review before merging:

```bash
skills/release/dismiss-stale-reviews.sh <owner> <repo> <pr-number>
```

Run it once Step 5's poll shows every bot's latest verdict clean. It emits a JSON summary of what it dismissed and what it left active, exits non-zero on API failure, and is idempotent on re-run. Which reviews it dismisses and which it leaves is the script's decision contract — see `skills/release/dismiss-stale-reviews.sh` header, not restated here (`rules/script-as-black-box.md`).

Before merging, capture the registry baseline so the post-merge check has something to compare against:

```bash
PRE=$(tessl plugin info <workspace>/<plugin> | grep "Latest Version" | awk '{print $NF}')
```

Pick the right cleanup path based on where you ran the skill from.

**(A) From the base checkout (no additional worktree):**

```bash
# Merge
gh pr merge <N> --merge --delete-branch

# Update local
git checkout main && git pull --ff-only

# Clean up local branch
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

**(B) From an additional worktree** (per `rules/agent-worktree-isolation.md`):

```bash
# Merge (running from inside the worktree is fine)
gh pr merge <N> --merge --delete-branch

# Return to the base checkout, fast-forward main
cd <path-to-base-checkout>
git checkout main && git pull --ff-only

# Tear down the worktree — directory + `.git/worktrees/` metadata
git worktree remove <path-to-worktree>

# Now safe: branch is no longer checked out anywhere and is fully merged
git branch -d <branch>

# Prune stale remote refs
git remote prune origin
```

Order in (B) is mandatory: `git branch -d` refuses to delete a branch that is checked out in any worktree, so the `git worktree remove` step must come before `git branch -d`. Reversing the order produces a "checked out at `<path>`" error and leaves a stranded branch.

After merge — per `rules/ci-safety.md`'s Always Watch CI duty extended through release:

- Verify the merge landed on main (`git pull --ff-only` succeeds; `git log -1 --oneline` shows the merge commit)
- Watch the publish workflow to a terminal state. Bind to the merge commit SHA + the `push` event, never to "latest on main". The publish workflow may take several seconds to be enqueued after merge, so the run-id lookup polls until the run is listed (2s interval, 30s budget):

  ```bash
  merge_sha=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
  run_id=$(skills/release/resolve-publish-run.sh <owner> <repo> "$merge_sha" "<publish-workflow-name>" | jq -r '.database_id')
  gh run watch "$run_id"
  ```

  No `--exit-status` on the watch: the publish-landed conjunction below reads the run conclusion explicitly, so letting `--exit-status` propagate a non-zero exit would short-circuit `set -e` wrappers before the conjunction runs.

  `gh pr view` returns the specific merge commit for this PR, unaffected by parallel merges. The resolver filters on `headSha == $merge_sha` AND `event == push` so manual `workflow_dispatch` runs sharing the SHA are excluded; it also retries on enqueue latency so the immediate post-merge `gh run list` doesn't race the publish workflow's enqueue and surface as "no run found". Output is `{"database_id": N}` per `rules/script-delegation.md` — extract with `jq -r '.database_id'`. The watch is a timing precondition for the conjunction below, not the gate
- Confirm the publish landed via the conjunction check — conjuncts 1 and 2 (resolved run's `conclusion == success` AND registry's `Latest Version > PRE`). Capture the emitted `current` version for the moderation check that follows:

  ```bash
  # Gate on the exit code — only a clean conjunction (rc 0) may proceed to
  # the moderation step. A non-zero rc (publish did not land, or a tool
  # error) stops the release here; do not fall through to moderation.
  landed=$(skills/release/verify-publish-landed.sh <workspace> <plugin> "$PRE" "$run_id") \
    || { echo "Publish not confirmed — $(jq -r '.reason // "see stderr"' <<<"$landed")" >&2; exit 1; }
  CURRENT=$(jq -r '.current' <<<"$landed")
  ```

  Output is exit-code-dependent: rc 0/1 emits the JSON envelope `{"ok": bool, "reason": "...", "run_conclusion": "...", "pre": "...", "current": "..."}` on stdout (parse it for the finding); rc 2 emits the stderr diagnostic (tool-state errors: run still in flight, gh/tessl unreachable). Exception: the missing-jq guard at rc 2 emits a minimal JSON envelope on stdout (the script can't use jq to format JSON when jq itself is absent) so wrappers that always parse stdout still see a parseable failure. Do not compare against a specific expected version. See `rules/ci-safety.md` for full release-contract semantics and failed-publish recovery
- Once conjuncts 1 and 2 hold, confirm moderation cleared — conjunct 3. A freshly published version can be install-blocked until its moderation state reaches `pass`; poll with exponential backoff (the script owns the backoff constants and the cleared/blocked decision):

  ```bash
  skills/release/verify-moderation-cleared.sh <workspace> <plugin> "$CURRENT"
  ```

  Exit 0 = moderation cleared. Exit 1 = blocked or still-pending at budget exhaustion — an unconfirmed release; surface it and do not report success. Exit 2 = tool-state error (tessl unreachable, jq missing). Never report the release confirmed until this clears. See `rules/ci-safety.md` for the full three-conjunct contract
- Report the outcome: merged PR URL, version published, registry + moderation confirmation

When this step is wrapped in a reusable script (e.g., `merge-and-cleanup.sh` that other devs run unattended), see `skills/release/SCRIPTING.md` for the gates the script must enforce.

Finish here — the skill is complete.
