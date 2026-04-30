---
alwaysApply: true
---

# Agent Worktree Isolation

## Why Isolate

- Multiple agents (or multiple instances of the same agent) operating on a single checkout race on the working tree, the index, branch state, and stash — one agent's `git checkout` mid-task overwrites files another is reading mid-edit
- Test runs, codegen, and lockfile updates collide silently — the agent that wins the last write produces a coherent commit, the loser produces garbage and reports success
- The fix is not coordination — it is physical separation. Each concurrent task gets its own working directory and its own branch checkout

## When the Rule Applies

- Any task that **writes to the repo** AND may run concurrently with another agent — branch work, file edits, scripted refactors, dependency upgrades, releases
- Read-only inspection (`Grep`, `Read`, `Glob`, non-mutating `Bash` like `git status`) on the shared checkout is fine; isolation becomes mandatory the moment the task crosses into mutating tools (`Edit`, `Write`, side-effecting `Bash`, branch creation)
- Single-machine, single-agent workflows still benefit from worktrees because they let you pause an in-flight task and start a different one without stash-and-restore games — but the rule only **requires** isolation when concurrency is possible

## How to Isolate

- Throughout this rule, "worktree" means an **additional working tree** created via `git worktree add` — distinct from the base checkout you cloned into, on its own branch, sharing the same `.git` object store. This is narrower than the generic "any git checkout" sense the word sometimes carries
- The Agent tool's `isolation: "worktree"` parameter is the canonical mechanism for spawned subagents — it provisions a fresh additional worktree on its own branch and cleans up on exit if the agent made no changes
- For non-agent parallel work or human-launched second sessions, use `git worktree add -b <task-branch> ../<repo>-<task>` to create an isolated checkout on a **new** branch (or `git worktree add ../<repo>-<task> <existing-branch>` to attach to one that already exists), then `cd` into it before any mutating operation
- Additional worktrees share the same `.git` object store — branch creation is cheap and disk usage stays small. The cost of isolation is trivial against the cost of a corrupted concurrent edit

## Cleanup

- A worktree's lifecycle ends when its branch merges or is abandoned — remove the worktree at that point, do not leave orphans accumulating in `git worktree list`
- Use `git worktree remove <path>` so the metadata under `.git/worktrees/` is cleaned up too — never `rm -rf` the directory directly, which strands the metadata and makes the path unreusable
- If `git worktree list` shows entries from finished tasks, the workflow that creates worktrees is missing its cleanup step — fix the workflow, not just the symptom

## Exception — Single-Reader Inspection

- A purely read-only inspection on the main checkout is permissible even with other agents active — the absence of writes means no race
- The exception evaporates the moment the inspection turns into "let me just fix this one thing" — at that point, isolate first and mutate inside the worktree
