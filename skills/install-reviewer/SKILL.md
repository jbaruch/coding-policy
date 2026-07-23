---
name: install-reviewer
description: >
  Opt one of the maintainer's own repositories into their same-owner `jbaruch/coding-policy`
  review setup (coding-policy holds their shared coding rules): scaffold a
  `.github/fleet-review-enabled` opt-in marker, a thin `.github/workflows/review-trigger.yml`
  (asks coding-policy to run an immediate PR-time review), and a
  `.github/copilot-instructions.md` scoping Copilot to the complementary code-quality lane,
  then open a PR. The trigger asks the same-owner coding-policy-fleet-reviewer GitHub App to
  review the PR; the marker enrolls the repo in the App's scheduled poll (the backstop). Both
  review against the maintainer's published `jbaruch/coding-policy` rules. The review credential
  stays only in coding-policy; the consumer sets one stable least-privilege `FLEET_DISPATCH_TOKEN`.
  Use when the user wants to add, install, enable, scaffold, set up, wire up, or enroll
  an automated policy review / PR reviewer / coding-policy reviewer in a consumer repo.
  Also use to upgrade, update, or refresh the reviewer files in a repo that already has
  them — the skill switches to override mode in that case.
---

# Install Reviewer Skill

Process steps in order. Do not skip ahead.

Opt one of the maintainer's own repositories into their same-owner `jbaruch/coding-policy` review setup — `jbaruch/coding-policy` is the maintainer's repository holding their shared coding rules. The reviewer is the `coding-policy-fleet-reviewer` GitHub App running in the maintainer's own `jbaruch/coding-policy`, using the Codex CLI on a ChatGPT subscription. A thin `.github/workflows/review-trigger.yml` asks that same-owner repo to run a single-PR review on each PR event (so the verdict lands before merge); the `.github/fleet-review-enabled` marker enrolls the repo in the App's scheduled poll, which is the backstop. The review credential stays only in `coding-policy`; the consumer holds the marker, the trigger workflow, and one stable least-privilege `FLEET_DISPATCH_TOKEN`. This skill commits those files plus the Copilot-lane charter and opens a PR.

Precondition: the App is installed on the account with access to this repo (installed on all repositories).

The skill runs in one of two modes determined by the user's request:

- **install** (default) — the consumer hasn't run the skill before, no reviewer artifacts exist. The current behavior of every step.
- **upgrade** (`--override`) — refresh previously-installed reviewer artifacts to the current plugin version
  - Trigger phrases: "upgrade", "update", "refresh", "pull latest reviewer setup", "override"
  - Pass `--override` to all five scripts: preflight, branch, scaffold, commit, push
  - Branch: `feat/upgrade-coding-policy-review`
  - Commit message: `ci(review): upgrade ...`
  - Preflight skips branch-clear checks; instead refuses if a rewritable target carries uncommitted edits the upgrade could clobber
  - Scaffold snapshots and restores all target files on failure

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

In **install mode**: if any reviewer file already exists (`.github/fleet-review-enabled`, `.github/workflows/review-trigger.yml`, or `.github/copilot-instructions.md`), stop and report that prior reviewer setup is present — re-run in upgrade mode to refresh it. scaffold.sh enforces this too (it refuses any pre-existing target in install mode). If none are present, proceed to Step 3.

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

Copies the opt-in files from the packaged template tree into the consumer, and documents the operator secret:

- `.github/fleet-review-enabled` — the opt-in marker; its presence enrolls the repo in the maintainer's same-owner review setup
- `.github/workflows/review-trigger.yml` — the thin PR-time trigger; asks the same-owner coding-policy to run an immediate single-PR review
- `.github/copilot-instructions.md` — the Copilot complementary-lane charter
- `.env.example` — appends a `FLEET_DISPATCH_TOKEN` entry carrying the repo's Actions-secrets settings URL (no-secrets rule); append-or-create, never overwrites prior variables

Install mode refuses if any of the three template targets already exists; upgrade mode overwrites them. `.env.example` is always append-or-create in both modes and is skipped when the secret is already documented. Emits a JSON summary on success (per-file `action` is `created|overwritten|appended|unchanged`); on failure it exits non-zero with a stderr diagnostic and restores every target to its prior contents. Idempotent: a re-run that changes nothing is a no-op. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/commit.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/commit.sh --override
```

Stages the reviewer files (`.github/fleet-review-enabled`, `.github/workflows/review-trigger.yml`, `.github/copilot-instructions.md`, and `.env.example`) and commits with the canonical message — `ci(review): add jbaruch/coding-policy PR review setup` in install mode, `ci(review): upgrade jbaruch/coding-policy PR review setup` in upgrade mode. Idempotent: emits `{"state": "no-op", …}` when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
# install mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/push.sh

# upgrade mode
.tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/push.sh --override
```

Pushes the appropriate branch (`feat/add-coding-policy-review` in install mode, `feat/upgrade-coding-policy-review` in upgrade mode) to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

`gh pr create` with title `ci(review): add jbaruch/coding-policy PR review setup` (install mode) or `ci(review): upgrade jbaruch/coding-policy PR review setup` (upgrade mode), and a body that follows the required content blocks (what this PR commits, how the review runs, the load indicator, conditional warnings section) defined at:

```text
skills/install-reviewer/PR_BODY_TEMPLATE.md
```

In upgrade mode, also include a brief diff line in the PR body naming the outgoing and incoming plugin versions so the human reviewer sees what's being refreshed.

Return the PR URL. If Step 1 emitted any warnings, surface them inline in your user-facing summary too (not only in the PR body). **Surface the one operator secret in your summary:** the trigger workflow needs a `FLEET_DISPATCH_TOKEN` repo secret — a fine-grained token the maintainer scopes to only their own `jbaruch/coding-policy` with `Actions: write` and nothing else (least privilege). Set it before the first PR after merge, or the PR-time review will not fire. The scheduled poll still reviews as a backstop. Finish here — the operator sets the secret and merges.
