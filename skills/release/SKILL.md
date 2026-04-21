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
    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps>
    ```

## Step 3 — Reason About Versioning

Decide the version bump:

- **Patch** (default): bug fixes, internal changes. Handled automatically by `tesslio/patch-version-publish` — no manifest update needed
- **Minor**: new features, backward-compatible additions. Update the version in the project manifest
- **Major**: breaking changes. Update the version in the project manifest

## Step 4 — Policy Review Fires Automatically

Pushing the PR branch triggers the runnable GitHub Actions workflow `.github/workflows/review.lock.yml` ("PR Policy Review") on the `pull_request` event. The `.github/workflows/review.md` file is the gh-aw source that compiles into that lock file via `gh aw compile`. The workflow runs on every `opened`, `synchronize`, and `reopened` — no explicit request mutation. The review is submitted by `github-actions[bot]` and uses OpenAI `gpt-5.4` via the gh-aw Codex engine, checking the diff against the in-tree `rules/*.md` from the PR head.

**Proceed immediately to Step 5 — do not stop after creating the PR.** The skill runs end-to-end: once `gh pr create` succeeds, the next action is always to start watching.

**Trial — keep Copilot in parallel.** During gh-aw validation, Copilot review is also requested via the GraphQL flow in `skills/release/COPILOT_REVIEW_GRAPHQL.md` (fetch PR node ID, call `requestReviews` with bot ID `BOT_kgDOCnlnWA`). Both reviews are treated as gating. This paragraph and `COPILOT_REVIEW_GRAPHQL.md` are deleted in a cleanup PR once gh-aw is validated on 1–2 PRs.

## Step 5 — Wait for Reviews + CI

Poll until all are complete:

- **CI + gh-aw check run**: `gh pr checks <N> --watch` (the gh-aw workflow appears as a check; must succeed)
- **Review states** — filter by bot login:
  ```bash
  gh api repos/<owner>/<repo>/pulls/<N>/reviews \
    --jq '.[] | select(.user.login == "github-actions[bot]" or .user.login == "copilot-pull-request-reviewer[bot]") | {user: .user.login, state: .state, submitted_at: .submitted_at}'
  ```
  - `github-actions[bot]` → gh-aw policy review
  - `copilot-pull-request-reviewer[bot]` → Copilot (trial only)
- **Inline comments**: `gh api repos/<owner>/<repo>/pulls/<N>/comments` (same filter by `.user.login`)

Interpreting review states (apply to each bot independently):
- `APPROVED` — no issues
- `CHANGES_REQUESTED` — comments need addressing
- `COMMENTED` — observations; read and decide per thread

If the gh-aw review check ran but no review was posted, inspect logs with `gh run view --log-failed`. Do not retry via GraphQL — gh-aw is event-triggered, not request-triggered.

## Step 6 — Address Feedback; No Re-request Needed

- **CI failures**: Fix every one, no exceptions
- **Review suggestions**: Apply what's right. Push back with a reply on anything that misreads scope or over-engineers
- **Reply on EVERY thread** — nothing left dangling:
  - Accepted: "Fixed in `<sha>`"
  - Declined: "Declining — `<reason>`"
- Push fixes to the same branch
- **Re-run is automatic**: `pull_request: synchronize` re-triggers the gh-aw workflow on every push — no manual re-request. During the trial, Copilot still needs a re-request via GraphQL per `COPILOT_REVIEW_GRAPHQL.md`.
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
