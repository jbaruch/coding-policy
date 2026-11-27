---
alwaysApply: true
---

# Dependency Management

## Required Read

- Read the named sections of `skills/coding-contracts/references/dependency-management.md` before deciding.
- Selecting a new dependency → `Stdlib First`.
- Changing dependencies or their manifest entries → `Declaration`, `Pinning`, `Freshness`, `Dependency Groups` and `CI Compatibility`.
- Choosing how to install → `No Vendoring` and `CI Compatibility`.
- Judging vendoring scope → `No Vendoring`.
- Deciding whether resolved state is a lock file → `Pinning`.
- Relying on a pinning exception → its named carve-out.

## Selection and Declaration

- Prefer stdlib.
- Declare every dependency.
- Pin dependencies unless a binding carve-out applies.
- Give each pin a renewal mechanism.

## Installation

- Never vendor this repository's dependencies.
