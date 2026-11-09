---
name: onboard-repo
description: >
  Add the files that run the `jbaruch/coding-policy` coding-rule review on a repo's pull
  requests: scaffold a `.github/fleet-review-enabled` marker, a
  `.github/workflows/review-trigger.yml` (on each PR, starts a review of that PR against the
  coding-policy rules plus an independent Claude code-quality review), and a
  `.env.example` entry, then open a PR. While the marker is present
  the same review also runs on a schedule as a backstop; both post as a review. The consumer
  sets one `FLEET_DISPATCH_TOKEN` secret that `review-trigger.yml` reads.
  Use when the user wants to add, install, enable, scaffold, set up, wire up, or enroll an
  automated policy review / PR reviewer / coding-policy reviewer in a consumer repo. Also use
  to upgrade or refresh the reviewer files in a repo that already has them (override mode).
---

# Onboard Repo Skill

Process steps in order. Do not skip ahead.

Commit the opt-in files that enroll this repository in the coding-policy fleet review, then open a PR. The reviewers' subscription credentials stay in coding-policy; the consumer holds only the marker, the trigger workflow and one `FLEET_DISPATCH_TOKEN` secret.

Precondition: both reviewer Apps (coding-policy-fleet-reviewer and jbaruch-claude-reviewer) are installed on the account with access to this repo.

The user's request picks the mode:

- **install** (default) — no reviewer artifacts exist yet
- **upgrade** — refresh previously installed reviewer artifacts to the current plugin version
  - Trigger phrases: "upgrade", "update", "refresh", "pull latest reviewer setup", "override"
  - Append `--override` to the script command in Steps 1, 2, 3, 5 and 6
  - Branch `feat/upgrade-coding-policy-review`, commit and PR title `ci(review): upgrade ...`

## Step 1 — Run Preflight Checks

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/preflight.sh"
```

Runs every precondition (git worktree, GitHub CLI install + auth, packaged templates present, origin remote, plus mode-dependent branch/target state) and returns one JSON object: `{"ok": bool, "override": bool, "failures": [...], "warnings": [...]}`. Upgrade mode also refuses a rewritable target carrying uncommitted edits.

- **Exit 0, empty `failures`** — every precondition passed; proceed to Step 2.
- **Exit 1, populated `failures`** — report each failure's `reason` verbatim and stop. Every failure carries a concrete recovery command.
- **Non-empty `warnings`** — informational only; never affects `ok` or the exit code. Report each `reason` verbatim alongside the Step 1 outcome and remember them for Step 7's PR body. Do not stop; proceed to Step 2.

## Step 2 — Establish Feature Branch

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/branch.sh"
```

Install mode creates `feat/add-coding-policy-review` from origin's default branch. Upgrade mode targets `feat/upgrade-coding-policy-review`, probing remote and local state to handle the fresh-clone-while-upgrade-PR-open case. Idempotent: emits `{"state": "already-on-branch", ...}` when HEAD already matches the target. Real `ls-remote`/`fetch` errors propagate verbatim with non-zero exit. Proceed immediately to Step 3.

## Step 3 — Scaffold Reviewer Artifacts

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/scaffold.sh"
```

Copies the opt-in files from the packaged template tree into the consumer, and documents the operator secret:

- `.github/fleet-review-enabled` — the opt-in marker; while present, the coding-policy review also runs on a schedule as a backstop
- `.github/workflows/review-trigger.yml` — on each PR, starts a review of that PR against the coding-policy rules
- `.env.example` — appends a `FLEET_DISPATCH_TOKEN` entry carrying the repo's Actions-secrets settings URL (no-secrets rule); append-or-create, never overwrites prior variables

Install mode refuses if either template target already exists; upgrade mode overwrites them. `.env.example` is skipped when the secret is already documented. Emits a JSON summary on success (per-file `action` is `created|overwritten|appended|unchanged`); on failure it exits non-zero with a stderr diagnostic and restores every target to its prior contents. Idempotent. Proceed immediately to Step 4.

## Step 4 — Apply tessl-artifact hygiene

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/tessl-hygiene.sh"
```

Sets every `jbaruch/*` dependency in `tessl.json` to `"version": "latest"` (third-party pins unchanged) and appends the tessl-generated-artifacts block to `.gitignore` when its marker is absent. `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` are not ignored. Emits `{"tessl_json":"pinned-latest|unchanged|absent","gitignore":"created|appended|unchanged"}`. Idempotent; takes no `--override` flag. Proceed immediately to Step 5.

## Step 5 — Commit

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/commit.sh"
```

Stages the reviewer files plus the Step 4 hygiene changes (`tessl.json`, `.gitignore`) when present, and commits with the canonical message — `ci(review): add jbaruch/coding-policy PR review setup`, or `ci(review): upgrade jbaruch/coding-policy PR review setup` in upgrade mode. Idempotent: emits `{"state": "no-op", …}` when the working tree already matches a prior successful run. If a pre-commit hook rejects the commit, the script exits non-zero — fix the hook's finding and re-run; do not `--no-verify`. Proceed immediately to Step 6.

## Step 6 — Push

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/onboard-repo/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/onboard-repo/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$KS/push.sh"
```

Pushes the mode's branch to origin with upstream tracking. Idempotent: emits `{"state": "up-to-date", …}` if origin already matches local HEAD. Proceed immediately to Step 7.

## Step 7 — Open PR

Resolve origin's repository, so the PR opens in the repo Step 6 pushed to and never in `gh`'s own default (`gh repo set-default`):

```bash
KS=.tessl/plugins/jbaruch/coding-policy/skills/release/; [ -d "$KS" ] || KS="$HOME/$KS"; [ -d "$KS" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) KS=skills/release/ ;; *) echo "coding-policy skill not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
python3 "$KS/origin-repo.py" .
```

Contract: see `skills/release/origin-repo.py` — top-of-file docstring. Emits `{ "repo", "owner", "name" }`. A non-zero exit stops the skill with its stderr diagnostic.

`gh pr create --repo <repo>`, `<repo>` from that output, with title `ci(review): add jbaruch/coding-policy PR review setup` (install mode) or `ci(review): upgrade jbaruch/coding-policy PR review setup` (upgrade mode), and a body that follows the required content blocks (what this PR commits, how the review runs, the load indicator, conditional warnings section) defined at:

```text
skills/onboard-repo/PR_BODY_TEMPLATE.md
```

In upgrade mode, also include a brief diff line in the PR body naming the outgoing and incoming plugin versions so the human reviewer sees what's being refreshed.

Return the PR URL. If Step 1 emitted any warnings, surface them inline in your user-facing summary too (not only in the PR body). **Surface the one operator secret in your summary:** `.github/workflows/review-trigger.yml` reads a `FLEET_DISPATCH_TOKEN` repo secret (a fine-grained token with `Actions: Read and write` on `jbaruch/coding-policy`). Set it before the first PR after merge, or the trigger will not run; the scheduled backstop still reviews. Finish here — the operator sets the secret and merges.
