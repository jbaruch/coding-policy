---
name: install-reviewer
description: >
  Set up the `jbaruch/coding-policy` PR reviewer in a consumer repository: commit an
  `AGENTS.md` `## Review guidelines` section that steers the OpenAI Codex code-review app
  to review every PR against the `jbaruch/coding-policy` rules, plus a
  `.github/copilot-instructions.md` scoping Copilot to the complementary code-quality lane,
  then open a PR. The Codex app runs on a ChatGPT subscription (no API key) and is enabled
  by the operator in the Codex UI — the PR's job is the two repo artifacts.
  Use when the user wants to add, install, enable, scaffold, set up, or wire up an automated
  policy review / PR reviewer / coding-policy reviewer in a consumer repo. Also use to
  upgrade, update, or refresh the reviewer artifacts in a repo that already has them — the
  skill switches to override mode in that case.
---

# Install Reviewer Skill

Set up the `jbaruch/coding-policy` PR reviewer (Codex policy app + Copilot lane) in a consumer repository. Process steps in order. Do not skip ahead.

The reviewer itself is the OpenAI Codex code-review GitHub App on a ChatGPT subscription, enabled by the operator in the Codex UI. This skill commits the two repo artifacts that steer it and opens a PR; Step 7 hands the operator the UI checklist.

The skill runs in one of two modes determined by the user's request:

- **install** (default) — the consumer hasn't run the skill before, no reviewer artifacts exist. The current behavior of every step.
- **upgrade** (`--override`) — refresh previously-installed reviewer artifacts to the current plugin version
  - Trigger phrases: "upgrade", "update", "refresh", "pull latest reviewer setup", "override"
  - Pass `--override` to all five scripts: preflight, branch, scaffold, commit, push
  - Branch: `feat/upgrade-coding-policy-review`
  - Commit message: `ci(review): upgrade ...`
  - Preflight skips branch-clear checks; instead refuses if a rewritable target carries uncommitted edits the upgrade could clobber
  - Scaffold snapshots and restores both target files on failure

## Step 1 — Run Preflight Checks

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/preflight.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/preflight.sh --override
```

Runs every precondition (git worktree, GitHub CLI install + auth, packaged templates present, origin remote, plus mode-dependent branch/target state) and returns one JSON object: `{"ok": bool, "override": bool, "failures": [...], "warnings": [...]}`.

- **Exit 0, empty `failures`** — every precondition passed; proceed to Step 2.
- **Exit 1, populated `failures`** — report each failure's `reason` verbatim and stop. Every failure carries a concrete recovery command.
- **Non-empty `warnings`** — informational only; never affects `ok` or the exit code. Report each `reason` verbatim alongside the Step 1 outcome and remember them for Step 7's PR body. Do not stop; proceed to Step 2.

## Step 2 — Refuse Overwrite (install mode only)

In **install mode**: if `AGENTS.md` already contains the managed `jbaruch/coding-policy review guidelines` block, or `.github/copilot-instructions.md` already exists, stop and report that prior reviewer setup (or a consumer-owned Copilot file) is present — re-run in upgrade mode to refresh it. If neither is present, proceed to Step 3.

In **upgrade mode**: skip this step. Preflight has verified the rewritable targets carry no uncommitted state the upgrade could clobber; scaffold.sh snapshots and restores them on failure.

## Step 3 — Establish Feature Branch

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/branch.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/branch.sh --override
```

Establishes the feature branch the rest of the steps commit on. Install mode creates `feat/add-coding-policy-review` from origin's default branch. Upgrade mode targets `feat/upgrade-coding-policy-review`, probing remote and local state to handle the fresh-clone-while-upgrade-PR-open case. Idempotent: emits `{"state": "already-on-branch", ...}` when HEAD already matches the target. Real `ls-remote`/`fetch` errors propagate verbatim with non-zero exit. Proceed immediately to Step 4.

## Step 4 — Scaffold Reviewer Artifacts

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/scaffold.sh --override
```

Writes the two reviewer artifacts from the packaged templates:

- `AGENTS.md` — ensures a marker-delimited `## Review guidelines` block (create the file if absent, append the block if the file exists without it, replace the block in place on re-run/upgrade). Any other `AGENTS.md` content is preserved.
- `.github/copilot-instructions.md` — the Copilot complementary-lane charter.

Emits a JSON summary on success; on failure it exits non-zero with a stderr diagnostic and restores both targets to their prior contents. Idempotent: a re-run that changes nothing is a no-op. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/commit.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/commit.sh --override
```

Stages the two artifacts (`AGENTS.md`, `.github/copilot-instructions.md`) and commits with the canonical message — `ci(review): add jbaruch/coding-policy PR review setup` in install mode, `ci(review): upgrade jbaruch/coding-policy PR review setup` in upgrade mode. Idempotent: emits `{"state": "no-op", …}` when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/push.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/push.sh --override
```

Pushes the appropriate branch (`feat/add-coding-policy-review` in install mode, `feat/upgrade-coding-policy-review` in upgrade mode) to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review setup` (install mode) or `ci(review): upgrade jbaruch/coding-policy PR review setup` (upgrade mode), and a body that follows the required content blocks (what this PR commits, the operator setup checklist, the load indicator, conditional warnings section) defined at:

```text
skills/install-reviewer/PR_BODY_TEMPLATE.md
```

In upgrade mode, also include a brief diff line in the PR body naming the outgoing and incoming plugin versions so the human reviewer sees what's being refreshed.

Return the PR URL. If Step 1 emitted any warnings, surface them inline in your user-facing summary too (not only in the PR body). **Also surface the operator setup checklist (block 2) in your summary** — the reviewer does not run until the operator completes those Codex-UI steps. Finish here — the user completes the Codex-UI setup and merges.
