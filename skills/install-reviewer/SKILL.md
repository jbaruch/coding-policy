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

Runs every precondition (git worktree, GitHub CLI install + auth, gh-aw extension, tile template, origin remote, local + remote branch clear) plus advisory checks, and returns one JSON object: `{"ok": bool, "failures": [...], "warnings": [...]}`. Exit 0 with empty `failures` means every precondition passed (proceed to Step 2). Exit 1 with a populated `failures` array means at least one precondition is missing — report every failure's `reason` verbatim and stop; each failure carries a concrete recovery command. The gh-aw extension is `github/gh-aw` — install with `gh extension install github/gh-aw` (the extension lives under the `github` org, not under this tile's owner). `warnings` is informational and never affects `ok` or the exit code: it surfaces advisory findings (e.g., a committed root-level `.mcp.json` that will break the Anthropic reviewer at runtime unless the consumer adds it to `.gitignore`). If the preflight returns any `warnings`, report every warning's `reason` verbatim to the user alongside the outcome of Step 1 — remember the warnings, you will surface them again in Step 7's PR body. Do not stop for warnings; proceed to Step 2.

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
- Explains the workflows install `jbaruch/coding-policy` at run time and review every PR against it, and that the OpenAI and Anthropic reviewers each self-gate on the PR's `Author-Model:` declaration so the active reviewer is cross-family whenever the declaration permits — when the declaration spans both paired families, or neither paired family, both reviewers run as the documented fallback (see `rules/author-model-declaration.md`)
- Lists the three repository secrets the user must set **before merge**: `OPENAI_API_KEY` (OpenAI billing account for Codex), `ANTHROPIC_API_KEY` (Anthropic billing account for Claude Code), and `TESSL_TOKEN` (created at https://tessl.io/account/api-keys)
- Notes that merging without all three secrets set will cause the workflows to fail on their first run
- If Step 1's preflight returned any `warnings`, includes a `## Action required before merge` section that reproduces every warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix (e.g., the `root-mcp-json-present` warning tells the consumer to add `.mcp.json` to `.gitignore` and keep a `.mcp.json.example` for local dev — without this, the Anthropic reviewer fails on its first run). The section exists so the consumer sees and acts on the finding instead of discovering it via a failed workflow
- Notes that each reviewer's verdict begins with a one-line load indicator (`"Policy loaded: N rule files from $HOME/.tessl/tiles/jbaruch/coding-policy/rules/ (installed tile)."`), making it obvious when the global `tessl install` ran cleanly vs when the policy didn't reach the runtime

Return the PR URL. If Step 1 emitted any warnings, also surface them inline in your user-facing summary (not only in the PR body) so the user sees them immediately without opening the PR. Finish here — the user validates the secrets, acts on any warnings, and merges.
