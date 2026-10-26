---
alwaysApply: true
---

# Agent Worktree Isolation

## Required Read

- Before writing repository files when another agent may work concurrently, or managing a task worktree lifecycle, read `skills/coding-contracts/references/agent-worktree-isolation.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Isolate before mutation in a separate git worktree and task branch.
- Main-checkout concurrent inspection stays read-only.
- Preserve unrelated work.
- Remove only this task's owned worktree through the binding cleanup order.
