---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: PR creation,
  Copilot code review, merge, and branch cleanup. Covers readiness checks,
  version reasoning, review requesting via GraphQL, feedback handling, and
  post-merge verification.
  Use when the user wants to open a pull request, ship code, request reviews,
  merge a branch, or handle post-merge cleanup on GitHub.
---

# Release Skill

Structured workflow for shipping code: PR creation, Copilot review, merge, and cleanup. Process each step in order — do not skip ahead.

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

## Step 4 — Request Copilot Review

**Must use GraphQL, not REST** — REST silently drops bot reviewers.

Use the queries in `skills/release/COPILOT_REVIEW_GRAPHQL.md`:
1. Fetch the PR's GraphQL node ID
2. Call `requestReviews` mutation with bot ID `BOT_kgDOCnlnWA`
3. If the bot ID is stale, use the fallback query to retrieve it from past reviews
4. Verify the request was accepted via the REST reviewers endpoint

## Step 5 — Wait for Review + CI

Poll until both are complete:

- **CI**: `gh pr checks <N> --watch`
- **Copilot review state**: `gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[].state'`
- **Inline comments**: `gh api repos/<owner>/<repo>/pulls/<N>/comments`

Interpreting review states:
- `APPROVED` — Copilot found no issues
- `CHANGES_REQUESTED` — Copilot left comments that need addressing
- `COMMENTED` — Copilot left observations; treat as comments to review

If the review never arrives, mention `@copilot` in a PR comment and re-request review using the GraphQL mutation.

## Step 6 — Address Feedback and Re-request

- **CI failures**: Fix every one, no exceptions
- **Copilot suggestions**: Apply what's right and reasonable. Push back with a reply on anything that misreads scope or over-engineers
- **Reply on EVERY thread** — nothing left dangling:
  - Accepted: "Fixed in `<sha>`"
  - Declined: "Declining — `<reason>`"
- Push fixes to the same branch
- **Re-request Copilot review** after pushing fixes (use the same GraphQL mutation from Step 4). Repeat Steps 5–6 until Copilot has zero comments — as many cycles as needed

## Step 7 — Merge + Cleanup

Only proceed when CI is green AND Copilot's latest review has zero comments AND all review threads have replies.

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
