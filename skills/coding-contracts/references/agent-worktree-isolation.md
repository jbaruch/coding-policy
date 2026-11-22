# Agent Worktree Isolation

## When to Isolate

- Any task that writes to the repo AND may run concurrently with another agent — branch work, file edits, scripted refactors, dependency upgrades, releases
- Read-only inspection on the shared checkout is permitted: `Grep`, `Read`, `Glob`, non-mutating `Bash` like `git status`
- Isolation becomes mandatory the moment the task crosses into mutating tools: `Edit`, `Write`, side-effecting `Bash`, branch creation
- Single-machine single-agent workflows may use worktrees but are not required to

## How to Isolate

- "Worktree" here means an additional working tree created via `git worktree add` — distinct from the base checkout, on its own branch, sharing the same `.git` object store
- For an independent session, use `git worktree add -b <task-branch> ../<repo>-<task>` to create an isolated checkout on a new branch (or `git worktree add ../<repo>-<task> <existing-branch>` to attach to one that already exists), then `cd` in before any mutating operation

## Cleanup

- A worktree's lifecycle ends when its branch merges or the worktree is abandoned
- An idle clean worktree whose HEAD origin holds counts as abandoned
- The agent removes its own worktree after the merge
- When the worktree's branch lands via `skills/release/SKILL.md` Step 7, the post-merge order is mandatory: `cd` back to the base checkout → fast-forward base `main` → check the worktree for a lock → `git worktree remove <worktree-path>` → `git branch -d <branch>`. Teardown precedes branch delete
- The lock check runs `skills/release/worktree-lock.py <worktree-path>` from the base checkout
- It prints `{"path", "locked", "reason"}`, with `reason` `null` for a lock that supplies none
- On exit 1, fix the cause its stderr names and re-run it before teardown
- Narrow exception for a locked worktree in that post-merge order. Preconditions:
  1. The worktree's branch landed via `skills/release/SKILL.md` Step 7.
  2. The lock check reports `"locked": true`.
- The locked worktree belongs to its lock holder
- Skip `git worktree remove` and `git branch -d` for it
- Never force its removal or unlock it
- Report the worktree path, its branch and the lock reason as left to its lock holder
- Report a `null` reason as `no reason supplied`
- The merge counts as delivered
- Every unlocked worktree follows the post-merge order
- Use `git worktree remove <path>`; never `rm -rf` the directory
- The session-start hook `skills/release/hooks/check-leftover-worktrees.sh` cleans the session's own repository, natively and under tessl
- It removes an idle clean worktree whose HEAD origin holds
- It deletes a local branch whose tip origin holds or whose tip origin's default branch contains
- GitHub's delete-branch-on-merge setting handles merged PR branches on origin
- The removal and deletion predicates live in `skills/release/lib/prune-worktrees.sh` (top-of-file docstring)
- A dirty or unpushed idle worktree is reported, never removed automatically
- A local branch holding commits origin lacks is never deleted automatically
- It is reported once idle
- A protected branch on origin is never touched or reported
- A reported item follows `rules/hook-action-reporting.md` Act on What It Names
- Leave no orphans in `git worktree list`

## Exception — Single-Reader Inspection

- Read-only inspection on the main checkout is permitted even with other agents active
- The exception evaporates the moment the inspection turns into "let me fix this one thing" — isolate first, mutate inside the worktree
