---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  automated policy review via gh-aw (with Copilot review kept in parallel during
  the trial), merge, and branch cleanup. Covers readiness checks, version
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

Pushing the PR branch automatically triggers the paired gh-aw policy reviewers (OpenAI + Anthropic, cross-family by author-model). See the trigger / self-gating / authorship mechanics, plus the trial-period Copilot details, at:

```text
skills/release/GH_AW_DETAILS.md
```

**Trial — keep Copilot in parallel.** During gh-aw validation, also request Copilot via `skills/release/request-copilot-review.sh <owner> <repo> <pr-number>`. Both reviews gate the merge.

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

Loop until `ci.status` is `success` (or `none` if no checks are configured) and no bot has `CHANGES_REQUESTED`. `COMMENTED` does NOT block the polling loop — exit it and proceed to Step 6. (`COMMENTED` with inline comments still requires reply-per-thread before the Step 7 merge gate, but that's a separate condition the operator confirms at merge time, not a poll-loop exit criterion.) If the gh-aw review check ran but no review was posted, inspect logs with `gh run view --log-failed`. Do not retry via GraphQL — gh-aw is event-triggered, not request-triggered.

## Step 6 — Address Feedback; No Re-request Needed

- **CI failures**: Fix every one, no exceptions
- **Review suggestions**: Apply what's right and reasonable. Push back on anything that misreads scope or over-engineers — but cite concrete evidence (file:line, log line, spec quote) when declining; never hand-wave
- **Reply on EVERY thread** — nothing left dangling. Use these exact opening literals so threads scan consistently:
  - Accepted: `Fixed in <sha>` (literal phrase; semantically-equivalent variants like `Done` or `Accepted and fixed` do not satisfy)
  - Declined: `Declining — <reason with cited evidence>` (em dash `—`, not a hyphen or period)
- Push fixes to the same branch
- **Re-run is automatic**: `pull_request: synchronize` re-triggers the gh-aw workflow on every push — no manual re-request. During the trial, Copilot still needs a re-request via `skills/release/request-copilot-review.sh` (same args as Step 4).
- Repeat Step 5 until every active bot review is `APPROVED` or `COMMENTED` with no blocking items, and every thread has a reply.

## Step 7 — Merge + Cleanup

Only proceed when:
- Step 5's poll returns `ci.status` green and no bot has `CHANGES_REQUESTED`, AND
- Every inline comment from Step 5's `inline_comments` count has a `Fixed in <sha>` or `Declining — <reason>` reply per Step 6 (verify by listing the PR's review comments — the poll script tracks counts, not reply state, so the operator confirms thread closure).

A `COMMENTED` review with zero inline comments is fully non-blocking; a `COMMENTED` review with inline comments is non-blocking once every thread has a reply.

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

After merge:
- Verify the merge landed on main
- Check that the publish CI workflow was triggered
- Report the outcome: merged PR URL, version published (if applicable)

When this step is wrapped in a reusable script (e.g., `merge-and-cleanup.sh` that other devs run unattended), see `skills/release/SCRIPTING.md` for the gates the script must enforce.

Finish here — the skill is complete.
