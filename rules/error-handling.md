---
alwaysApply: true
---

# Error Handling

## Required Read

- Before authoring, modifying or reviewing exception handlers, shell failure paths or process boundaries, read `skills/coding-contracts/references/error-handling.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Catch specific exceptions; use catch-all only at an authorized outer boundary. Shell scripts use set -euo pipefail unless the full contract permits otherwise. Never silently suppress failures or log secrets; emit actionable diagnostics.
