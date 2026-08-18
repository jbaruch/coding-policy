# Release-Skill Scripting Reference

When the agent's interactive Step-2 (PR create) and Step-7 (merge + cleanup) instructions get encoded into a reusable script that other devs run unattended (`release.sh`, `merge-and-cleanup.sh`, etc.), the script has to carry the SAME gates the interactive agent does — otherwise the script bypasses the conventions this skill exists to hold. Pulled out of SKILL.md to keep the main workflow scannable.

## Wrapping Step 2 (PR create) in a script

The script MUST enforce all of:

- **Step-1 readiness gate** — run the project's tests AND linter before pushing. Fail loudly if either fails. Never push from a script that doesn't gate on green tests + clean lint.
- **Conventional-commits PR title** — construct or validate the title against `<type>(<scope>): <imperative summary>`. Taking the title as a raw argument and passing it straight to `gh pr create` defeats the convention; either build the title from `<type>`, `<scope>`, `<summary>` inputs, or regex-validate the supplied title before push.

## Wrapping Step 7 (merge + cleanup) in a script

The script MUST enforce all of:

- **Pre-merge wait + gates** — block until the PR is ready with `skills/release/watch-pr-reviews.sh <owner> <repo> <pr-number>` (script-owned interval/budget; emits `.watch.result` — `ready` on exit 0, `changes_requested` / `ci_failure` / `dirty` on exit 0, `pending_at_budget` on exit 1). Never hand-roll the poll loop or wrap the watch in an invented `timeout`. Then re-check the same conditions the interactive agent checks at merge time: CI status is `success` (or `none`), every bot review is `APPROVED` or non-blocking `COMMENTED`, no review thread is unresolved. Fail loudly if any gate is red instead of proceeding.
- **Non-empty review body halts unattended merge** — whether a review body is substantive is a reasoning call the script can't make (`rules/script-delegation.md`). Any non-empty `reviews.*.body` must surface for an agent/human read regardless of state — `APPROVED` included, not just `COMMENTED` — mirroring Step 7's `Every non-empty reviews.*.body` gate. A wrapper that merges on state alone reproduces the unread-body hole `rules/reviewer-feedback-reading.md` exists to close.
- **Safe local-branch delete** — `git branch -d <branch>` (refuses to drop unmerged work), never `git branch -D`. The whole point of the cleanup is "ship and tidy"; clobbering an unmerged branch with `-D` defeats the safety the merge gate just established.
- **Worktree-aware teardown** — if the wrapper may be invoked from inside an additional worktree (per `rules/agent-worktree-isolation.md`), it must detect that case and run the worktree variant of cleanup: `cd` to the base checkout, fast-forward `main`, `git worktree remove <worktree-root>`, then `git branch -d <branch>`. Three derivations the wrapper needs:
  - **Detection** — `[ "$(git rev-parse --git-dir)" != "$(git rev-parse --git-common-dir)" ]`; true means you're in an additional worktree
  - **Current worktree root** — `git rev-parse --show-toplevel` (required by `git worktree remove`, which refuses subdirectory paths with `not a working tree`)
  - **Base checkout root** — `git worktree list --porcelain | awk '/^worktree / {print $2; exit}'` (the first `worktree` entry is always the main checkout)
  Skipping this branch makes `git branch -d` fail with a confusing "checked out at `<path>`" error and leaves the worktree directory plus `.git/worktrees/` metadata stranded; the script must surface a coherent teardown, not that error.
- **Pre-merge baseline** — BEFORE invoking `gh pr merge`, capture the registry's `Latest Version` as a baseline (Tessl: `tessl plugin info <workspace>/<plugin>`; other registries: `npm view`, `pypi` JSON, etc.). The baseline feeds the conjunction check below.
- **Post-merge: main advanced** — confirm `main` actually advanced to the merge commit before any further verification step runs.
- **Post-merge: resolve THIS publish run** — derive the merge commit SHA via `gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid'` (NOT `git log -1` on `main`), then `skills/release/resolve-publish-run.sh <owner> <repo> <merge-sha> <workflow-name>` (filters on `headSha + push`, polls until enqueued, emits `{"database_id": N}`). Never select by "latest on main" or any branch-only/limit-1 heuristic.
- **Post-merge: watch to terminal state** — extract the run ID with `jq -r '.database_id'` and `gh run watch <id>` on that exact run. Never reduce this to "check it triggered".
- **Post-merge: conjunction check (conjuncts 1 + 2)** — `skills/release/verify-publish-landed.sh <workspace> <plugin> <pre-baseline> <run-id>` exits 0 iff the resolved run's `conclusion` is `success` AND the registry's `Latest Version` is strictly greater than the pre-merge baseline. The script must NOT bypass the conjunction script's exit code (e.g., by parsing `current > pre` alone).
- **No expected-version derivation** — do NOT derive an expected version from the merge SHA's manifest and compare against it.
- **Post-merge: moderation clear (conjunct 3)** — moderation is a post-publish install gate; the wrapper must wait for the published version's moderation state to reach `pass` via `skills/release/verify-moderation-cleared.sh <workspace> <plugin> <version>` (exponential backoff, fail loud at budget — the script owns the backoff constants and the cleared/blocked decision). A still-pending or blocked state at budget exhaustion exits non-zero; the wrapper must NOT report the release confirmed. Query the real moderation state from the registry — never fabricate one as a hedge for a failed publish.

## Publish-pipeline CHANGELOG stamping (not an agent step)

`skills/release/stamp-changelog.py` runs in the publish workflow (`.github/workflows/publish.yml`), not from any SKILL.md step — the agent never invokes it. It stamps the publish-on-merge CHANGELOG so un-headed `### ` entries get a `## <version> — <date>` heading matching the version being published.

Contract:

- **Inputs** — `--changelog` (default `CHANGELOG.md`), `--manifest` (default `.tessl-plugin/plugin.json`, then `tile.json`), `--latest` (skip the registry query; CI omits it and queries `tessl plugin info`), `--date` (default today, UTC)
- **Side effect** — rewrites the CHANGELOG in place; the stamp-changelog action then commits and self-pushes it (via `commit-stamp.sh`) with the CI-skip marker, decoupled from the publish step's exit (#284)
- **Idempotent** — no-op when the top section is already under a `## ` heading
- **Exit** — non-zero on a malformed version, a missing manifest field, or a non-404 `tessl plugin info` failure (auth/network is surfaced, not masked)
- The version-computation and stamping logic is the source of truth in the script's module docstring and `compute_version` / `stamp_changelog` — do not restate it here

The workflow step must run BEFORE the publish step (`smart-publish`) so the stamped heading and the assigned version stay in lockstep.

## Enrolling a repo in the publish pipeline (not an agent step)

The publish workflow above is a thin caller of the canonical reusable pipeline (`.github/workflows/publish-plugin.yml`). Wiring a new plugin repo onto that pipeline — or migrating one off a bespoke `tesslio/patch-version-publish` workflow — is a documented maintainer step, not part of the release flow. The caller template, the input guide, the Dependabot pin renewal, and the migration procedure live in the reference doc (repo-internal; `docs/` is `.tesslignore`d, so read it from the repo, not an install):

```text
docs/fleet-publish-setup.md
```

## Why these gates have to be in the script, not just the skill prose

A scripted run is unattended. Anything the interactive agent enforces by reading SKILL.md only protects the sessions where the SKILL.md is in the agent's context. A script that doesn't carry the same gates internally hands every consumer a sharper version of the bypass-by-automation problem.
