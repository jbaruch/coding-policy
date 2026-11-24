---
alwaysApply: true
---

# Error Handling

## Required Read

- Before handling errors, choosing fallbacks, writing or reviewing logging, exception handlers, shell failure paths or process boundaries, or relying on any exception to this rule, read `skills/coding-contracts/references/error-handling.md` in full.

## Boundary

- Catch specific exceptions.
- Use catch-all only at an authorized outer boundary.
- Shell scripts use set -euo pipefail unless the full contract permits otherwise.
- Never silently suppress failures.
- Never log secrets.
- Emit actionable diagnostics.
