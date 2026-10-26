---
alwaysApply: true
---

# Testing Standards

## Required Read

- Before writing, modifying or reviewing shipped code, tests or test-exemption claims, read `skills/coding-contracts/references/testing-standards.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Every shipped module has tests.
- Assert outcomes with fixed independent fixtures.
- Keep tests deterministic.
- A test exemption needs every binding precondition.
