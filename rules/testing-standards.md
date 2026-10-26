---
alwaysApply: true
---

# Testing Standards

## Required Read

- Before writing or reviewing shipped code, building fixtures, running tests or relying on a test exemption, or relying on any exception to this rule, read `skills/coding-contracts/references/testing-standards.md` in full.
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
