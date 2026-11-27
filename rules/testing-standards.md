---
alwaysApply: true
---

# Testing Standards

## Required Read

- Read the named sections of `skills/coding-contracts/references/testing-standards.md` before acting.
- Adding shipped modules or deciding coverage → `Scope` and `Coverage`.
- Authoring, changing or reviewing tests → `Assertions`, `Determinism`, `Fixtures` and `Independence`.
- Diagnosing flaky tests → `Determinism` and `Independence`.
- Relying on an instrument-tree exemption → `Scope`.
- Relying on another testing exception → its named carve-out.

## Coverage

- Test every shipped module unless a binding carve-out applies.
- Follow the project's test file naming convention.

## Test Behavior

- Assert outcomes, not implementation details.
- Use fixed inputs outside binding carve-outs.
- Control time-dependent inputs outside binding carve-outs.
- Keep tests independent of order and shared mutable state.
