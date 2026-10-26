---
alwaysApply: true
---

# Stateful Artifacts

## Required Read

- Before creating, reading, writing, migrating, renaming or removing persistent state artifacts, read `skills/coding-contracts/references/stateful-artifacts.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Require an owner, versioned schema and reader/writer contract. Verify recalled state against its live source. Only the owner migrates.
