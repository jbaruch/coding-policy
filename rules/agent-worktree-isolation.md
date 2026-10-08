---
alwaysApply: true
---

# Agent Worktree Isolation

## When to Isolate

- Any task that writes to the repo AND may run concurrently with another agent — branch work, file edits, scripted refactors, dependency upgrades, releases
- Read-only inspection on the shared checkout is permitted: `Grep`, `Read`, `Glob`, non-mutating `Bash` like `git status`
- Isolation becomes mandatory the moment the task crosses into mutating tools: `Edit`, `Write`, side-effecting `Bash`, branch creation
- Single-machine single-agent workflows may use worktrees but are not required to

## How to Isolate

- "Worktree" here means an additional working tree created via `git worktree add` — distinct from the base checkout, on its own branch, sharing the same `.git` object store
- Delegation follows `rules/agent-team-operation.md` Delegation
- In a Herdr round the foreman provisions each writer's worktree under `skills/herdr-foreman/references/team-operation.md` Writers and Checkouts
- For independent sessions outside a Herdr round, use `git worktree add -b <task-branch> ../<repo>-<task>` to create an isolated checkout on a new branch (or `git worktree add ../<repo>-<task> <existing-branch>` to attach to one that already exists), then `cd` in before any mutating operation

## Cleanup

- A worktree's lifecycle ends when its branch merges or the worktree is abandoned
- An idle clean worktree whose HEAD origin holds counts as abandoned
- Outside a Herdr team round, the agent removes its own worktree after the merge
- When the worktree's branch lands via `skills/release/SKILL.md` Step 7, the post-merge order is mandatory: `cd` back to the base checkout → fast-forward base `main` → `git worktree remove <worktree-path>` → `git branch -d <branch>`. Teardown precedes branch delete
- Use `git worktree remove <path>`; never `rm -rf` the directory
- The session-start hook `hooks/check-leftover-worktrees.sh` cleans the session's own repository in a native session
- Under tessl (portable mode), it reports from a dry run in a main checkout and deletes nothing
- Under tessl, it skips a linked worktree
- In a Herdr worker session, it deletes nothing
- It removes an idle clean worktree whose HEAD origin holds
- It deletes a local branch whose tip origin holds or whose tip origin's default branch contains
- It deletes an unprotected branch on origin merged into the default branch with no open pull request
- The removal and deletion predicates live in `skills/herdr-foreman/prune-worktrees.sh` and `skills/herdr-foreman/prune-remote-branches.sh` (top-of-file docstrings)
- A dirty or unpushed idle worktree is reported, never removed automatically
- A local branch holding commits origin lacks is never deleted automatically
- It is reported once idle
- An unmerged branch on origin with no pull request is never deleted automatically
- It is reported once stale
- A protected branch on origin is never touched or reported
- A reported item follows `rules/hook-action-reporting.md` Act on What It Names
- In a Herdr team round, see `skills/herdr-foreman/references/team-operation.md` Writers and Checkouts
- Leave no orphans in `git worktree list`

## Exception — Single-Reader Inspection

- Read-only inspection on the main checkout is permitted even with other agents active
- The exception evaporates the moment the inspection turns into "let me fix this one thing" — isolate first, mutate inside the worktree
