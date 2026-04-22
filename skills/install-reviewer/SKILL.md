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

## Step 1 — Run Preflight Checks

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh
```

Runs every precondition (git worktree, GitHub CLI install + auth, gh-aw extension, tile template, origin remote, local + remote branch clear) and returns one JSON object. Exit 0 with `{"ok": true, "failures": []}` means all checks passed; exit 1 with a populated `failures` array means at least one precondition is missing. Each failure carries a concrete recovery command for the user. If exit non-zero, report every failure's `reason` verbatim and stop. If exit zero, proceed immediately to Step 2.

## Step 2 — Refuse Overwrite

If **either** `.github/workflows/review.md` **or** `.github/workflows/review.lock.yml` already exists in the repo, stop and report that prior review setup is present. Do not overwrite either file — the lock alone (source removed) or the source alone (mid-authoring) both indicate deliberate in-progress configuration that the skill would destroy by compiling over it. If neither file exists, proceed immediately to Step 3.

## Step 3 — Create Feature Branch

`git checkout -b feat/add-coding-policy-review` from the repo's default branch. Proceed immediately to Step 4.

## Step 4 — Scaffold Workflow Files

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh
```

Creates `.github/workflows/` if missing, copies the packaged template into `review.md`, compiles it via `gh aw compile review` to produce `review.lock.yml`, and ensures `.gitattributes` marks the lock file as generated (`linguist-generated=true`, `merge=ours`) per `rules/file-hygiene.md`. Emits a JSON summary on success; exits non-zero with a stderr diagnostic and rolls back every artifact it touched (including restoring `actions-lock.json` from a snapshot) on compile failure. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/commit.sh
```

Stages the four scaffolded files (`review.md`, `review.lock.yml`, `actions-lock.json`, `.gitattributes`) and commits with the canonical message `ci(review): add jbaruch/coding-policy PR review workflow`. Idempotent: emits `{"state": "no-op", …}` on re-run when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/push.sh
```

Pushes `feat/add-coding-policy-review` to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review workflow` and a body that:
- Explains the workflow installs `jbaruch/coding-policy` at run time and reviews every PR against it
- Lists the two repository secrets the user must set **before merge**: `OPENAI_API_KEY` (OpenAI billing account for Codex) and `TESSL_TOKEN` (created at https://tessl.io/account/api-keys)
- Notes that merging without the secrets set will cause the workflow to fail on its first run

Return the PR URL. Finish here — the user validates the secrets and merges.
