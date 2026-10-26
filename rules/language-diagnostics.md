---
alwaysApply: true
---

# Language Diagnostics

## Required Read

- Before working on code, configuring diagnostic engines or handing off code changes, read `skills/coding-contracts/references/language-diagnostics.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Use the existing language engine in the editor and as a zero-findings deterministic CI/local gate. Fix findings or attach a stated-cause inline suppression; never silence a whole file.
