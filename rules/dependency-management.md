---
alwaysApply: true
---

# Dependency Management

## Required Read

- Before writing code, selecting, installing or changing dependencies and manifests, or relying on a dependency carve-out, read `skills/coding-contracts/references/dependency-management.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Prefer stdlib.
- Declare and pin dependencies with a renewal mechanism.
- Never vendor this repository's dependencies.
- A floating exception needs every binding precondition.
