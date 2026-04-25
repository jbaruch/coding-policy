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

Structured workflow for shipping code: PR creation, automated policy review, merge, and cleanup. Process each step in order — do not skip ahead.

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
- **Author-Model is mandatory** per `rules/author-model-declaration.md`. Use the exact model ID you (the agent) are running under (e.g., `claude-opus-4-7`, `gpt-5.4`), or `human` for a hand-authored PR, or list every contributing model space-separated for mixed authorship (e.g., `human claude-opus-4-7`). The paired gh-aw reviewers read this line to pick the cross-family reviewer; omitting it blocks the PR — each reviewer emits `REQUEST_CHANGES` immediately and exits without reading the diff.

**When wrapping this step in a reusable script** (e.g., `release.sh` that other devs run): the script itself must enforce the Step 1 readiness gate (run tests AND linter before pushing; fail if either fails) and must construct or validate the PR title against `<type>(<scope>): <imperative summary>` — taking the title as a raw argument and passing it straight to `gh pr create` bypasses the convention this skill exists to hold. The script must also pass through the `Author-Model:` line.

Proceed immediately to Step 3 — do not wait for reviews before reasoning about version.

## Step 3 — Reason About Versioning

Decide the version bump:

- **Patch** (default): bug fixes, internal changes. Handled automatically by `tesslio/patch-version-publish` — no manifest update needed
- **Minor**: new features, backward-compatible additions. Update the version in the project manifest
- **Major**: breaking changes. Update the version in the project manifest

## Step 4 — Policy Review Fires Automatically

Pushing the PR branch triggers two paired GitHub Actions workflows — `.github/workflows/review-openai.lock.yml` (Codex / `gpt-5.4`) and `.github/workflows/review-anthropic.lock.yml` (Claude Code / `claude-opus-4-7`) — on the `pull_request` event. Each lock file is compiled from its `.md` gh-aw source via `gh aw compile`. Both run on every `opened`, `synchronize`, and `reopened`. Each reviewer self-gates on the PR's `Author-Model:` declaration per `rules/author-model-declaration.md`: the same-family reviewer posts a one-line "skipping: self-review-bias" COMMENT and exits; the cross-family reviewer checks the diff against the in-tree `rules/*.md` from the PR head. Reviews are submitted by `github-actions[bot]`.

**Proceed immediately to Step 5 — do not stop after creating the PR.** The skill runs end-to-end: once `gh pr create` succeeds, the next action is always to start watching.

**Trial — keep Copilot in parallel.** During gh-aw validation, also request Copilot:

```bash
skills/release/request-copilot-review.sh <owner> <repo> <pr-number>
```

Uses GraphQL (REST drops bot reviewers), falls back to discovering the bot ID from recent reviews if the pinned `BOT_kgDOCnlnWA` goes stale, verifies Copilot is in `requested_reviewers`. Exits non-zero on failure; emits a JSON summary on success. Both reviews gate the merge. This paragraph and the script are retired in a cleanup PR once gh-aw is validated on 1–2 PRs.

## Step 5 — Poll PR State

Capture a single JSON snapshot of CI status, bot review states, and inline comment counts:

```bash
skills/release/poll-pr-reviews.sh <owner> <repo> <pr-number>
```

The script returns:
- `ci.status` — `pending | success | failure | none` (the gh-aw workflow appears here as a check once it has run)
- `reviews.gh_aw.state` and `reviews.copilot.state` — latest review per bot (`APPROVED | CHANGES_REQUESTED | COMMENTED | none`)
- `inline_comments.gh_aw` and `inline_comments.copilot` — top-level inline comment counts

Interpreting review states (per bot independently):
- `APPROVED` — no issues
- `CHANGES_REQUESTED` — comments need addressing
- `COMMENTED` — observations; read and decide per thread

Loop until `ci.status` is `success` (or `none` if no checks are configured) and neither review state is `CHANGES_REQUESTED`. If the gh-aw review check ran but no review was posted, inspect logs with `gh run view --log-failed`. Do not retry via GraphQL — gh-aw is event-triggered, not request-triggered.

## Step 6 — Address Feedback; No Re-request Needed

- **CI failures**: Fix every one, no exceptions
- **Review suggestions**: Apply what's right and reasonable. Push back on anything that misreads scope or over-engineers — but cite concrete evidence (file:line, log line, spec quote) when declining; never hand-wave
- **Reply on EVERY thread** — nothing left dangling. Use these exact opening literals so threads scan consistently across the team:
  - Accepted — reply begins with the literal phrase `Fixed in <sha>` (substitute the real commit hash, or keep the `<sha>` marker if you're drafting a template). `Done`, `Resolved`, `Applied the fix`, or `Accepted and fixed` do NOT satisfy the convention, even when semantically equivalent
  - Declined — reply begins with the literal phrase `Declining — <reason with cited evidence>`. The separator is an em dash (`—`, U+2014) followed by a space, NOT a period, a hyphen (`-`), or an en dash (`–`). Follow the em dash with a concrete reference (file:line, quoted spec clause, linked prior issue)
- Push fixes to the same branch
- **Re-run is automatic**: `pull_request: synchronize` re-triggers the gh-aw workflow on every push — no manual re-request. During the trial, Copilot still needs a re-request via `skills/release/request-copilot-review.sh` (same args as Step 4).
- Repeat Step 5 until every active bot review is `APPROVED` or `COMMENTED` with no blocking items, and every thread has a reply.

## Step 7 — Merge + Cleanup

Only proceed when CI is green AND the latest gh-aw review has zero blocking comments AND (during trial) the latest Copilot review has zero comments AND all review threads have replies.

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

**When wrapping this step in a reusable script** (e.g., `merge-and-cleanup.sh` that other devs run): the script must enforce the same pre-merge gates the agent does — CI status is `success` (or `none`), every bot review is `APPROVED` or non-blocking `COMMENTED`, no review thread is unresolved — and fail loudly if any gate is red instead of proceeding with the merge. The local-branch delete uses `git branch -d` (safe delete that refuses to drop unmerged work), never `git branch -D`. Post-merge, the script verifies main advanced to the merge commit and confirms the publish/release workflow fired before printing the final summary — fire-and-forget scoring zero because a silent publish failure is the exact problem the cleanup automation exists to catch.

Finish here — the skill is complete.
