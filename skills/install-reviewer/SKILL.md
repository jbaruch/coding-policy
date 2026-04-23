---
name: install-reviewer
description: >
  Scaffold the `jbaruch/coding-policy` gh-aw PR review workflows into a consumer
  repository: copies the packaged paired workflow templates (OpenAI + Anthropic
  reviewers), compiles them with `gh aw`, and opens a PR. After merge, every
  pull request in the repo is reviewed against the latest published
  `jbaruch/coding-policy` rules by the reviewer whose family differs from the
  PR's declared author model — avoiding self-review bias per
  `rules/author-model-declaration.md`.
  Use when the user wants to add, install, enable, scaffold, set up, or wire up
  an automated policy review / PR reviewer / coding-policy CI reviewer in a
  consumer repo.
---

# Install Reviewer Skill

Scaffold the gh-aw PR policy reviewer pair (OpenAI + Anthropic) into a consumer repository. Steps are sequential — complete each before moving to the next.

## Step 1 — Run Preflight Checks

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh
```

Runs every precondition (git worktree, GitHub CLI install + auth, gh-aw extension, tile template, origin remote, local + remote branch clear) and returns one JSON object. Exit 0 with `{"ok": true, "failures": []}` means all checks passed; exit 1 with a populated `failures` array means at least one precondition is missing. Each failure carries a concrete recovery command for the user. If exit non-zero, report every failure's `reason` verbatim and stop. If exit zero, proceed immediately to Step 2.

## Step 2 — Refuse Overwrite

If **any** of `.github/workflows/review-openai.md`, `.github/workflows/review-openai.lock.yml`, `.github/workflows/review-anthropic.md`, or `.github/workflows/review-anthropic.lock.yml` already exists in the repo, stop and report that prior review setup is present. Do not overwrite any of these files — a lock alone (source removed) or a source alone (mid-authoring) both indicate deliberate in-progress configuration that the skill would destroy by compiling over it. If none exist, proceed immediately to Step 3.

## Step 3 — Create Feature Branch

`git checkout -b feat/add-coding-policy-review` from the repo's default branch. Proceed immediately to Step 4.

## Step 4 — Scaffold Workflow Files

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh
```

Creates `.github/workflows/` if missing, copies both packaged templates into `review-openai.md` and `review-anthropic.md`, compiles them via `gh aw compile review-openai review-anthropic` to produce the matching `.lock.yml` files, and ensures `.gitattributes` marks the lock files as generated (`linguist-generated=true`, `merge=ours`) per `rules/file-hygiene.md`. Emits a JSON summary on success; exits non-zero with a stderr diagnostic and rolls back every artifact it touched (including restoring `actions-lock.json` from a snapshot) on compile failure — the two templates scaffold atomically: either both land or neither does. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/commit.sh
```

Stages the six scaffolded files (`review-openai.md`, `review-openai.lock.yml`, `review-anthropic.md`, `review-anthropic.lock.yml`, `actions-lock.json`, `.gitattributes`) and commits with the canonical message `ci(review): add jbaruch/coding-policy PR review workflows`. Idempotent: emits `{"state": "no-op", …}` on re-run when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/push.sh
```

Pushes `feat/add-coding-policy-review` to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review workflows` and a body that:
- Explains the workflows install `jbaruch/coding-policy` at run time and review every PR against it, and that the OpenAI and Anthropic reviewers each self-gate on the PR's `Author-Model:` declaration so the active reviewer is always cross-family (see `rules/author-model-declaration.md`)
- Lists the three repository secrets the user must set **before merge**: `OPENAI_API_KEY` (OpenAI billing account for Codex), `ANTHROPIC_API_KEY` (Anthropic billing account for Claude Code), and `TESSL_TOKEN` (created at https://tessl.io/account/api-keys)
- Notes that merging without all three secrets set will cause the workflows to fail on their first run
- Notes that if the repo has a `.mcp.json` at its root declaring stdio MCP servers that depend on binaries unavailable inside gh-aw's awf sandbox (e.g., `tessl mcp start`), the Anthropic reviewer will fail to launch those servers and gh-aw will fail the job — fix by adding `.mcp.json` to `.gitignore` and keeping a `.mcp.json.example` file for local dev
- Notes that each reviewer's verdict now begins with a one-line load indicator (`"Policy loaded: N rule files from .tessl/tiles/jbaruch/coding-policy/rules/ (installed tile)."`), making it obvious when `tessl install` ran cleanly vs when the policy didn't reach the runtime

Return the PR URL. Finish here — the user validates the secrets and merges.
