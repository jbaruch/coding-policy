# Release-Skill Scripting Reference

When the agent's interactive Step-2 (PR create) and Step-7 (merge + cleanup) instructions get encoded into a reusable script that other devs run unattended (`release.sh`, `merge-and-cleanup.sh`, etc.), the script has to carry the SAME gates the interactive agent does — otherwise the script bypasses the conventions this skill exists to hold. Pulled out of SKILL.md to keep the main workflow scannable.

## Wrapping Step 2 (PR create) in a script

The script MUST enforce all of:

- **Step-1 readiness gate** — run the project's tests AND linter before pushing. Fail loudly if either fails. Never push from a script that doesn't gate on green tests + clean lint.
- **Conventional-commits PR title** — construct or validate the title against `<type>(<scope>): <imperative summary>`. Taking the title as a raw argument and passing it straight to `gh pr create` defeats the convention; either build the title from `<type>`, `<scope>`, `<summary>` inputs, or regex-validate the supplied title before push.
- **`**Author-Model:**` line, preferred bold form** — the script should preserve or emit the bold marker (`**Author-Model:**`) per `rules/author-model-declaration.md`'s "Explicit (preferred)" form. The reviewer prompts also accept bare `Author-Model:` as a fallback, so the script won't break by emitting the unstyled form, but a wrapper that drops the marker line entirely turns every reviewer run into an early `REQUEST_CHANGES` (the rule's "Neither present is a policy violation" clause).

## Wrapping Step 7 (merge + cleanup) in a script

The script MUST enforce all of:

- **Pre-merge gates** — same conditions the interactive agent checks: CI status is `success` (or `none`), every bot review is `APPROVED` or non-blocking `COMMENTED`, no review thread is unresolved. Fail loudly if any gate is red instead of proceeding.
- **Safe local-branch delete** — `git branch -d <branch>` (refuses to drop unmerged work), never `git branch -D`. The whole point of the cleanup is "ship and tidy"; clobbering an unmerged branch with `-D` defeats the safety the merge gate just established.
- **Worktree-aware teardown** — if the wrapper may be invoked from inside an additional worktree (per `rules/agent-worktree-isolation.md`), it must detect that case and run the worktree variant of cleanup: `cd` to the base checkout, fast-forward `main`, `git worktree remove <worktree-root>`, then `git branch -d <branch>`. Three derivations the wrapper needs:
  - **Detection** — `[ "$(git rev-parse --git-dir)" != "$(git rev-parse --git-common-dir)" ]`; true means you're in an additional worktree
  - **Current worktree root** — `git rev-parse --show-toplevel` (required by `git worktree remove`, which refuses subdirectory paths with `not a working tree`)
  - **Base checkout root** — `git worktree list --porcelain | awk '/^worktree / {print $2; exit}'` (the first `worktree` entry is always the main checkout)
  Skipping this branch makes `git branch -d` fail with a confusing "checked out at `<path>`" error and leaves the worktree directory plus `.git/worktrees/` metadata stranded; the script must surface a coherent teardown, not that error.
- **Pre-merge baseline** — BEFORE invoking `gh pr merge`, capture the registry's `Latest Version` as a baseline (Tessl: `tessl tile info <workspace>/<tile>`; other registries: `npm view`, `pypi` JSON, etc.). The baseline feeds the conjunction check in (3) below.
- **Post-merge verification** — three confirmations the script must perform, in order: (1) `main` actually advanced to the merge commit; (2) the publish/release workflow reached a **terminal state** — derive the merge commit SHA from `gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid'` (NOT `git log -1` on `main`), resolve the publish run via `skills/release/resolve-publish-run.sh <owner> <repo> <merge-sha> <workflow-name>` (filters on `headSha + push`, polls until enqueued, emits `{"database_id": N}`). Extract the ID with `jq -r '.database_id'`, then `gh run watch <id>` on **that exact run**. Never select by "latest on main" or any branch-only/limit-1 heuristic. Never reduce this to "check it triggered"; (3) **publish-landed conjunction** — `skills/release/verify-publish-landed.sh <workspace> <tile> <pre-baseline> <run-id>` emits `{"ok": bool, "reason": "...", "run_conclusion": "...", "pre": "...", "current": "..."}` and exits 0 iff both conjuncts hold: the resolved run's `conclusion` is `success` AND the registry's `Latest Version` is strictly greater than the pre-merge baseline. The script must NOT bypass the conjunction script's exit code (e.g., by parsing `current > pre` alone). Do **not** derive an expected version from the merge SHA's manifest and compare against it. The script must NOT encode "moderation hold", "moderation queue", or "moderation rejection" as expected intermediate states or retry/wait conditions (per `rules/ci-safety.md`, the Tessl registry never rejects a published version). Per `rules/ci-safety.md`'s extended Always Watch CI duty.

## Why these gates have to be in the script, not just the skill prose

A scripted run is unattended. Anything the interactive agent enforces by reading SKILL.md only protects the sessions where the SKILL.md is in the agent's context. A script that doesn't carry the same gates internally hands every consumer a sharper version of the bypass-by-automation problem.
