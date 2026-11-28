---
alwaysApply: true
---

# Language Diagnostics

## Required Read

- Read the named sections of `skills/coding-contracts/references/language-diagnostics.md` before acting.
- Choosing or configuring diagnostics → `Enable the Language Server` and `Resolve Modules First`.
- Setting up or changing the CI gate or its scope → `Gate It Deterministically`.
- Enabling a gate on a previously unchecked tree → `Adopting on a Dirty Tree`.
- Suppressing a finding → `Findings Are Non-Dismissible Without Cause`.

## Editor Diagnostics

- Use language diagnostics in the editor and as you work.
- Use the existing engine unless there is consensus to add another.
- Fix findings or attach a stated-cause inline suppression.
- Never silence a whole file.

## Deterministic Gate

- Gate CI at zero findings with the same engine in batch mode before tests.
- Run the CI gate command before handing off code changes.
- Scope the handoff check to changed code only if the gate supports it.
