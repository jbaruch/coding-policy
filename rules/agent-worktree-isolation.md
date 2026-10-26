---
alwaysApply: true
---

# Agent Worktree Isolation

## Required Read

- Before concurrent repository inspection or writes, worktree or branch lifecycle operations, or cleanup reporting, or relying on any exception to this rule, read `skills/coding-contracts/references/agent-worktree-isolation.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Isolate writes that may run concurrently with another agent in a separate git worktree and task branch.
- Main-checkout concurrent inspection stays read-only.
- Preserve unrelated work.
- Remove only this task's owned worktree through the binding cleanup order.
- During cleanup, never touch or report a protected origin branch.
- A single-machine single-agent workflow may proceed without a worktree.
