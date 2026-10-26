---
alwaysApply: true
---

# Dependency Management

## Required Read

- Before writing code, changing dependencies or manifests, or relying on a dependency carve-out, read `skills/coding-contracts/references/dependency-management.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Prefer stdlib; declare and pin dependencies with a renewal mechanism. Never vendor this repository's dependencies. A floating exception needs every binding precondition.
