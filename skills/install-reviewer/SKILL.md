---
name: install-reviewer
description: >
  Scaffold the `jbaruch/coding-policy` gh-aw PR review workflow into a consumer
  repository: copies the packaged workflow template, compiles it with `gh aw`,
  and opens a PR. After merge, every pull request in the repo is reviewed
  against the latest published `jbaruch/coding-policy` rules.
  Use when the user wants to add, install, enable, scaffold, set up, or wire up
  an automated policy review / PR reviewer / coding-policy CI reviewer in a
  consumer repo.
---

# Install Reviewer Skill

Scaffold the gh-aw PR policy reviewer into a consumer repository. Steps are sequential — complete each before moving to the next.

## Step 1 — Verify Prerequisites

- `gh --version` returns a version (GitHub CLI installed)
- `gh auth status` succeeds (GitHub CLI authenticated). If not authenticated, stop and ask the user to run `gh auth login`
- `gh aw --version` returns a version (gh-aw extension installed). If missing: `gh extension install github/gh-aw`
- `.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/review-workflow.md` exists (the template is present via `tessl install jbaruch/coding-policy`). If missing, stop and ask the user to run `tessl install jbaruch/coding-policy` first

If any check fails, report which and stop.

## Step 2 — Refuse Overwrite

If `.github/workflows/review.md` already exists in the repo, stop and report that the reviewer is already installed. Do not overwrite.

## Step 3 — Create Feature Branch

`git checkout -b feat/add-coding-policy-review` from the repo's default branch.

## Step 4 — Ensure Workflows Directory

`mkdir -p .github/workflows` — idempotent; needed for repos that have no workflows yet.

## Step 5 — Copy Template

`cp .tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/review-workflow.md .github/workflows/review.md`

## Step 6 — Compile Workflow

`gh aw compile review` — produces `.github/workflows/review.lock.yml`, the file GitHub Actions actually runs.

## Step 7 — Commit

Stage both files and commit with message: `ci: add jbaruch/coding-policy PR review workflow`. If a pre-commit hook rejects either file, fix and re-commit — do not `--no-verify`.

## Step 8 — Push

`git push -u origin feat/add-coding-policy-review`

## Step 9 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review workflow` (follows `rules/commit-conventions.md` `<type>(<scope>): <imperative summary>` format) and a body that:
- Explains the workflow installs `jbaruch/coding-policy` at run time and reviews every PR against it
- Lists the two repository secrets the user must set **before merge**: `OPENAI_API_KEY` (OpenAI billing account for Codex) and `TESSL_TOKEN` (created at https://tessl.io/account/api-keys)
- Notes that merging without the secrets set will cause the workflow to fail on its first run

Return the PR URL. Do not merge — the user validates the secrets and merges.
