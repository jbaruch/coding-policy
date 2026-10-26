---
alwaysApply: true
---

# Error Handling

## Required Read

- Before handling errors, choosing fallbacks, writing or reviewing logging, exception handlers, shell failure paths or process boundaries, read `skills/coding-contracts/references/error-handling.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Catch specific exceptions.
- Use catch-all only at an authorized outer boundary.
- Shell scripts use set -euo pipefail unless the full contract permits otherwise.
- Never silently suppress failures.
- Never log secrets.
- Emit actionable diagnostics.
