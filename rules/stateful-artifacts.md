---
alwaysApply: true
---

# Stateful Artifacts

## Required Read

- Before creating, reading, writing, migrating, renaming or removing persistent state artifacts, or relying on any exception to this rule, read `skills/coding-contracts/references/stateful-artifacts.md` in full.

## Boundary

- Require an owner, versioned schema and reader/writer contract.
- Verify recalled state against its live source.
- Only the owner migrates.
