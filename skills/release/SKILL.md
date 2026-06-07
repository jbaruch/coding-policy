---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  dual-lens automated review (gh-aw for `rules/*.md` compliance + Copilot for
  doc accuracy and cross-step consistency), merge, and branch cleanup. Covers
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
- Self-audit the diff against every governing rule or skill whose domain covers the touched paths (e.g., `evals/` → `rules/plugin-evals.md` and `skills/eval-authoring/SKILL.md`; auto-loaded prose in `rules/` or `skills/` → `rules/context-writing-style.md`; new scripts → `rules/script-delegation.md` and `rules/testing-standards.md`)
- Grep the diff for the literal markers each governing rule or skill names:
  - banned connectives `because`, `therefore`, `since`, etc. per `rules/context-writing-style.md`
  - the `outer-boundary-process-contract` token per `rules/error-handling.md`
  - `max_score` weight sums (must sum to 100) per `skills/eval-authoring/SKILL.md`
- Run any local check the rule or skill prescribes:
  - `jq '[.checklist[].max_score] | add' <criteria.json>` must print `100`, and the weights must not be uniformly distributed (per `skills/eval-authoring/SKILL.md`)
  - `bash -n <script>` must exit 0 on shell scripts
  - the script's own fixture test must pass
- The paired cross-family reviewer is a backstop, not the first read
- If anything fails, fix it before proceeding

## Step 2 — Create PR

- Push the branch: `git push -u origin <branch>`
- Create the PR with `gh pr create`:
  - **Title**: `<type>(<scope>): <imperative summary>`
  - **Body**:
    ```
    **Author-Model:** <model-id(s) space-separated, or `human`>

    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps>
    ```
- **Author-Model is mandatory** per `rules/author-model-declaration.md`. Use the exact model ID you're running under (e.g., `claude-opus-4-7`, `gpt-5.4`), `human` for a hand-authored PR, or every contributing model space-separated for mixed authorship.

When this step is wrapped in a reusable script (e.g., `release.sh` that other devs run unattended), see the script-wrapping gates at:

```text
skills/release/SCRIPTING.md
```

Proceed immediately to Step 3.

## Step 3 — Reason About Versioning

Decide the bump per semver. Patch is the default and is handled automatically by `tesslio/patch-version-publish` — only update the manifest version for minor or major.

## Step 4 — Policy Review Fires Automatically

Opening the PR, or pushing further commits to an existing PR, automatically triggers the paired gh-aw policy reviewers (OpenAI + Anthropic, cross-family by author-model). The workflows are bound to the `pull_request` event (`opened` / `synchronize` / `reopened` / `edited`); a plain `git push` to a non-PR branch does NOT fire them. See the trigger / self-gating / authorship mechanics at:

```text
skills/release/GH_AW_DETAILS.md
```

**Also request Copilot.** Copilot is a deliberate second reviewer with a different lens — gh-aw enforces `rules/*.md` compliance, Copilot reads for doc accuracy, cross-step consistency, and ambiguity. Both reviewers gate the merge:

```bash
skills/release/request-copilot-review.sh <owner> <repo> <pr-number>
```

Proceed immediately to Step 5.

## Step 5 — Poll PR State

Capture a single JSON snapshot of CI status, bot review states, and inline comment counts:

```bash
skills/release/poll-pr-reviews.sh <owner> <repo> <pr-number>
```

The script returns:
- `ci.status` — `pending | success | failure | none` (the gh-aw workflow appears here as a check once it has run)
- `reviews.gh_aw.state` and `reviews.copilot.state` — latest review per bot (`APPROVED | CHANGES_REQUESTED | COMMENTED | none`)
- `inline_comments.gh_aw` and `inline_comments.copilot` — top-level inline comment counts
- `merge_state.status` and `merge_state.mergeable` — GitHub's merge-readiness state (e.g., `CLEAN`/`MERGEABLE`, `DIRTY`/`CONFLICTING`)

Each poll, check `merge_state` first:

- If `merge_state.mergeable` is `CONFLICTING` or `merge_state.status` is `DIRTY`, exit the loop immediately, rebase onto current `main`, resolve conflicts, and force-push; resume polling once the next push fires the missed `pull_request:` workflows.
- If `merge_state.mergeable` is `UNKNOWN`, keep polling — GitHub is still computing mergeability after the most recent push.
- Once `merge_state.mergeable` is `MERGEABLE`, continue polling and exit when `ci.status` is `success` (or `none` if no checks are configured) AND no bot has `CHANGES_REQUESTED`, then proceed to Step 6. `COMMENTED` does NOT block the polling-loop exit; Step 7's merge gate separately requires every inline comment thread to have a reply.

If the gh-aw review check ran but no review was posted, inspect logs with `gh run view --log-failed`. Do not retry via GraphQL — gh-aw is event-triggered.

## Step 6 — Address Feedback; No Re-request Needed

- **CI failures**: Fix every one
- **Review suggestions**: Apply what's right. Push back on anything that misreads scope — cite concrete evidence (file:line, log line, spec quote) when declining
- **Reply on EVERY thread.** Use these exact opening literals:
  - Accepted: `Fixed in <sha>` (literal phrase; `Done` / `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not hyphen or period)
- Push fixes to the same branch
- **gh-aw re-runs automatically on every push** (`pull_request: synchronize`); Copilot needs a manual re-request each push via `skills/release/request-copilot-review.sh` (same args as Step 4).
- Repeat Step 5 until every active bot review is `APPROVED` or `COMMENTED` with no blocking items, and every thread has a reply.

## Step 7 — Merge + Cleanup

Only proceed when:
- Step 5's poll returns `ci.status` as `success` (or `none` if no checks are configured) AND `merge_state.mergeable` is `MERGEABLE` (`UNKNOWN` is not acceptable — GitHub hasn't finished computing mergeability) AND `merge_state.status` is not `DIRTY` AND no bot has `CHANGES_REQUESTED`, AND
- Both `reviews.gh_aw.state` and `reviews.copilot.state` are NOT `none` — i.e., each gating reviewer has actually posted a review. A reviewer that hasn't run yet leaves `state: none` and `inline_comments: 0`, which would otherwise satisfy the no-CHANGES_REQUESTED check vacuously, AND
- Every inline comment from Step 5's `inline_comments` count has a `Fixed in <sha>` or `Declining — <reason>` reply per Step 6 (verify by listing the PR's review comments — the poll script tracks counts, not reply state, so the operator confirms thread closure).

A `COMMENTED` review with zero inline comments is fully non-blocking; a `COMMENTED` review with inline comments is non-blocking once every thread has a reply.

Before merging, capture the registry baseline so the post-merge check has something to compare against:

```bash
PRE=$(tessl tile info <workspace>/<tile> | grep "Latest Version" | awk '{print $NF}')
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
  landed=$(skills/release/verify-publish-landed.sh <workspace> <tile> "$PRE" "$run_id") \
    || { echo "Publish not confirmed — $(jq -r '.reason // "see stderr"' <<<"$landed")" >&2; exit 1; }
  CURRENT=$(jq -r '.current' <<<"$landed")
  ```

  Output is exit-code-dependent: rc 0/1 emits the JSON envelope `{"ok": bool, "reason": "...", "run_conclusion": "...", "pre": "...", "current": "..."}` on stdout (parse it for the finding); rc 2 emits the stderr diagnostic (tool-state errors: run still in flight, gh/tessl unreachable). Exception: the missing-jq guard at rc 2 emits a minimal JSON envelope on stdout (the script can't use jq to format JSON when jq itself is absent) so wrappers that always parse stdout still see a parseable failure. Do not compare against a specific expected version. See `rules/ci-safety.md` for full release-contract semantics and failed-publish recovery
- Once conjuncts 1 and 2 hold, confirm moderation cleared — conjunct 3. A freshly published version can be install-blocked until its moderation state reaches `pass`; poll with exponential backoff (the script owns the backoff constants and the cleared/blocked decision):

  ```bash
  skills/release/verify-moderation-cleared.sh <workspace> <tile> "$CURRENT"
  ```

  Exit 0 = moderation cleared. Exit 1 = blocked or still-pending at budget exhaustion — an unconfirmed release; surface it and do not report success. Exit 2 = tool-state error (tessl unreachable, jq missing). Never report the release confirmed until this clears. See `rules/ci-safety.md` for the full three-conjunct contract
- Report the outcome: merged PR URL, version published, registry + moderation confirmation

When this step is wrapped in a reusable script (e.g., `merge-and-cleanup.sh` that other devs run unattended), see `skills/release/SCRIPTING.md` for the gates the script must enforce.

Finish here — the skill is complete.
