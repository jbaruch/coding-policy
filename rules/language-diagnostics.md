---
alwaysApply: true
---

# Language Diagnostics

## Required Read

- Before working on code, configuring diagnostic engines or handing off code changes, or relying on any exception to this rule, read `skills/coding-contracts/references/language-diagnostics.md` in full.

## Boundary

- Use the existing language engine in the editor.
- Run its zero-findings deterministic gate in CI and before handoff.
- Fix findings or attach a stated-cause inline suppression.
- Never silence a whole file.
