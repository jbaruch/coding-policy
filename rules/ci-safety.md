---
alwaysApply: true
---

# CI Safety

## Required Read

- Before editing CI configuration, pushing, watching checks or reviews, merging, publishing, or relying on a CI carve-out, read `skills/coding-contracts/references/ci-safety.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Do not change CI outside the authorized scope. Never skip failing tests, bypass review or push directly to protected branches outside a binding carve-out. Watch every push to its required terminal gates.
