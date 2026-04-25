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

Runs every precondition (git worktree, GitHub CLI install + auth, gh-aw extension at minimum version, tile template, origin remote, local + remote branch clear) and returns one JSON object: `{"ok": bool, "failures": [...], "warnings": [...]}`.

- **Exit 0, empty `failures`** — every precondition passed; proceed to Step 2.
- **Exit 1, populated `failures`** — report each failure's `reason` verbatim and stop. Every failure carries a concrete recovery command. The gh-aw extension is `github/gh-aw` (lives under the `github` org, not the tile owner) and must be v0.71.0+. Install with `gh extension install github/gh-aw --pin v0.71.0` — the unpinned form would land on the latest *stable* release (currently below v0.71.0; everything from v0.69.0 onward is marked prerelease) and fail the version check.
- **Non-empty `warnings`** — informational only; never affects `ok` or the exit code. Report each `reason` verbatim alongside the Step 1 outcome and remember them for Step 7's PR body. Do not stop; proceed to Step 2.

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

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review workflows` and a body that follows the four required content blocks (cross-family rule explainer, required secrets, load-indicator note, conditional warnings section) defined at:

```text
skills/install-reviewer/PR_BODY_TEMPLATE.md
```

Return the PR URL. If Step 1 emitted any warnings, surface them inline in your user-facing summary too (not only in the PR body) so the user sees them immediately without opening the PR. Finish here — the user validates the secrets, acts on any warnings, and merges.
