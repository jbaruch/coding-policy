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

Structured workflow for shipping code: PR creation, Copilot review, merge, and cleanup.

## Steps

### 1. Verify Readiness

- Confirm you're on a feature branch (not `main`/`master`)
- Run the test suite — all tests must pass
- Run the linter — no warnings or errors
- If anything fails, fix it before proceeding

### 2. Create PR

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

### 3. Reason About Versioning

Decide the version bump:

- **Patch** (default): bug fixes, internal changes. Handled automatically by `tesslio/patch-version-publish` — no manifest update needed
- **Minor**: new features, backward-compatible additions. Update the version in the project manifest
- **Major**: breaking changes. Update the version in the project manifest

### 4. Request Copilot Review

**Must use GraphQL, not REST** — REST silently drops bot reviewers.

```bash
# Get PR node ID
PR_NODE_ID=$(gh api graphql -f query='
  query {
    repository(owner: "<owner>", name: "<repo>") {
      pullRequest(number: <N>) { id }
    }
  }
' --jq '.data.repository.pullRequest.id')

# Request Copilot review
gh api graphql -f query='
  mutation {
    requestReviews(input: {
      pullRequestId: "'"$PR_NODE_ID"'",
      botIds: ["BOT_kgDOCnlnWA"]
    }) {
      clientMutationId
    }
  }
'
```

**Fallback**: If the bot ID goes stale, retrieve it from a past Copilot review:
```bash
gh api graphql -f query='
  query {
    repository(owner: "<owner>", name: "<repo>") {
      pullRequests(last: 20) {
        nodes {
          reviews(first: 10) {
            nodes {
              author { ... on Bot { id login } }
            }
          }
        }
      }
    }
  }
'
```

**Verify** the request was accepted:
```bash
gh api repos/<owner>/<repo>/pulls/<N> --jq '.requested_reviewers[].login'
```

### 5. Wait for Review + CI

Poll until both are complete:

- **CI**: `gh pr checks <N> --watch`
- **Copilot review state**: `gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[].state'`
- **Inline comments**: `gh api repos/<owner>/<repo>/pulls/<N>/comments`

### 6. Address Feedback

- **CI failures**: Fix every one, no exceptions
- **Copilot suggestions**: Apply what's right and reasonable. Push back with a reply on anything that misreads scope or over-engineers
- **Reply on EVERY thread** — nothing left dangling:
  - Accepted: "Fixed in `<sha>`"
  - Declined: "Declining — `<reason>`"
- Push fixes to the same branch

### 7. Merge + Cleanup

Only proceed when CI is green AND all review threads have replies.

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
