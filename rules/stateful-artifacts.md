---
alwaysApply: true
---

# Stateful Artifacts

## Required Read

- Before creating, reading, writing, migrating, renaming or removing persistent state artifacts, read `skills/coding-contracts/references/stateful-artifacts.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Require an owner, versioned schema and reader/writer contract.
- Verify recalled state against its live source.
- Only the owner migrates.
