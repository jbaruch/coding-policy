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
  consumer repo. Also use to upgrade, update, refresh, or pull the latest
  reviewer templates into a consumer repo that already has the scaffold
  installed — the skill switches to override mode in that case.
---

# Install Reviewer Skill

Scaffold the gh-aw PR policy reviewer pair (OpenAI + Anthropic) into a consumer repository. Steps are sequential — complete each before moving to the next.

The skill runs in one of two modes determined by the user's request:

- **install** (default) — the consumer hasn't run the skill before, no scaffolded reviewer files exist. The current behavior of every step.
- **upgrade** (`--override`) — the consumer ran the skill on a prior tile version and now wants to refresh to the current one. Triggered by user phrases like "upgrade", "update", "refresh", "pull latest reviewer templates", or "override". Each script in this skill takes an optional `--override` flag; pass it to ALL FOUR scripts (preflight, scaffold, commit, push) when in upgrade mode, none of them when in install mode. The branch name and commit message change accordingly (`feat/upgrade-coding-policy-review` and `ci(review): upgrade ...`); preflight skips the branch-clear checks (the upgrade branch may legitimately exist from a prior in-flight upgrade) and instead refuses if any of the four target files have uncommitted local edits, so the consumer commits or stashes before the scaffold replaces them; scaffold snapshots and restores all four target files on compile failure.

## Step 1 — Run Preflight Checks

```bash
# install mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh

# upgrade mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/preflight.sh --override
```

Runs every precondition (git worktree, GitHub CLI install + auth, gh-aw extension at minimum version, tile template, origin remote, plus mode-dependent branch state — install mode requires the install branch to be clear locally and remotely; upgrade mode skips that and instead refuses if any of the four target files have local edits or are untracked) and returns one JSON object: `{"ok": bool, "override": bool, "failures": [...], "warnings": [...]}`.

- **Exit 0, empty `failures`** — every precondition passed; proceed to Step 2.
- **Exit 1, populated `failures`** — report each failure's `reason` verbatim and stop. Every failure carries a concrete recovery command. The gh-aw extension is `github/gh-aw` (lives under the `github` org, not the tile owner) and must be v0.71.0+. Install with `gh extension install github/gh-aw --pin v0.71.0` — the unpinned form would land on the latest *stable* release (currently below v0.71.0; everything from v0.69.0 onward is marked prerelease) and fail the version check.
- **Non-empty `warnings`** — informational only; never affects `ok` or the exit code. Report each `reason` verbatim alongside the Step 1 outcome and remember them for Step 7's PR body. Do not stop; proceed to Step 2.

## Step 2 — Refuse Overwrite (install mode only)

In **install mode**: if **any** of `.github/workflows/review-openai.md`, `.github/workflows/review-openai.lock.yml`, `.github/workflows/review-anthropic.md`, or `.github/workflows/review-anthropic.lock.yml` already exists in the repo, stop and report that prior review setup is present. Do not overwrite any of these files — a lock alone (source removed) or a source alone (mid-authoring) both indicate deliberate in-progress configuration that the skill would destroy by compiling over it. If none exist, proceed immediately to Step 3.

In **upgrade mode**: skip this step entirely. The targets are expected to exist; preflight's `no-dirty-target-edits` check has already verified the consumer's working tree is clean on those paths, and scaffold.sh will snapshot and restore them on compile failure.

## Step 3 — Create or Switch Feature Branch

In **install mode**: `git checkout -b feat/add-coding-policy-review` from the repo's default branch.

In **upgrade mode**: target branch is `feat/upgrade-coding-policy-review`. Pick the path based on what's already there — running this skill from a fresh clone while a prior upgrade PR is still open is a real case, and naively running `git checkout -b` from default would create a divergent local branch that can't fast-forward push.

- Probe remote first with `git ls-remote --exit-code --heads origin feat/upgrade-coding-policy-review` (exit 0 → remote branch exists; exit 2 → it doesn't; any other exit → real network/auth error, abort and report).
- If remote branch exists, run `git fetch origin feat/upgrade-coding-policy-review` to surface it as `origin/feat/upgrade-coding-policy-review`. Real fetch errors (network, permission) propagate verbatim.
- Local branch exists → `git checkout feat/upgrade-coding-policy-review` and append commits.
- Local doesn't exist but remote does → `git checkout -b feat/upgrade-coding-policy-review --track origin/feat/upgrade-coding-policy-review` so the local branch tracks the remote and the upcoming push is fast-forward.
- Neither exists → `git checkout -b feat/upgrade-coding-policy-review` from the repo's default branch.

Proceed immediately to Step 4.

## Step 4 — Scaffold Workflow Files

```bash
# install mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh

# upgrade mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh --override
```

Creates `.github/workflows/` if missing, copies both packaged templates into `review-openai.md` and `review-anthropic.md`, compiles them via `gh aw compile review-openai review-anthropic` to produce the matching `.lock.yml` files, and ensures `.gitattributes` marks the lock files as generated (`linguist-generated=true`, `merge=ours`) per `rules/file-hygiene.md`. Emits a JSON summary on success; exits non-zero with a stderr diagnostic and rolls back every artifact it touched on compile failure (in upgrade mode the rollback restores the prior contents of all four target files from snapshots in addition to restoring `actions-lock.json`). The two templates scaffold atomically: either both land or neither does. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
# install mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/commit.sh

# upgrade mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/commit.sh --override
```

Stages the six scaffolded files (`review-openai.md`, `review-openai.lock.yml`, `review-anthropic.md`, `review-anthropic.lock.yml`, `actions-lock.json`, `.gitattributes`) and commits with the canonical message — `ci(review): add jbaruch/coding-policy PR review workflows` in install mode, `ci(review): upgrade jbaruch/coding-policy PR review workflows` in upgrade mode. Idempotent: emits `{"state": "no-op", …}` on re-run when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
# install mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/push.sh

# upgrade mode
.tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/push.sh --override
```

Pushes the appropriate branch (`feat/add-coding-policy-review` in install mode, `feat/upgrade-coding-policy-review` in upgrade mode) to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review workflows` (install mode) or `ci(review): upgrade jbaruch/coding-policy PR review workflows` (upgrade mode), and a body that follows the four required content blocks (cross-family rule explainer, required secrets, load-indicator note, conditional warnings section) defined at:

```text
skills/install-reviewer/PR_BODY_TEMPLATE.md
```

In upgrade mode, also include a brief diff line in the PR body showing what's being upgraded — the consumer's outgoing tile version (read from their committed lock-file header banner if discoverable, or stated as "previous" if not) and the new tile version (the version the agent is currently running under). The human reviewer should be able to see what's being upgraded without diffing every line of YAML.

Return the PR URL. If Step 1 emitted any warnings, surface them inline in your user-facing summary too (not only in the PR body) so the user sees them immediately without opening the PR. Finish here — the user validates the secrets, acts on any warnings, and merges.
