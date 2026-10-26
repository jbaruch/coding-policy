---
alwaysApply: false
applyTo: "rules/**/*.md, skills/**/SKILL.md, README.md, CHANGELOG.md — when writing prose in plugin context artifacts"
description: Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format
---

# Context Writing Style

## Required Read

- Before writing plugin prose, maintaining its CHANGELOG, judging policy edge cases, debugging directives or auditing rules, or relying on any exception to this rule, read `skills/coding-contracts/references/context-writing-style.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Keep loaded rules and skills compact.
- Retain binding constraints.
- Move detail to explicitly read references.

## Full-Contract Task Router

- Narrow exception for a full-contract task router.
- Applies when a rule routes its complete single-concept contract to a co-shipped skill reference.
- Preconditions (all required):
  1. Preserve the rule's applicability, title and affected-action trigger.
  2. Require the full reference before every affected action, including reliance on an exception.
  3. Preserve every original directive, carve-out trigger, applies-when clause, precondition, pointer and reset in the reference.
  4. Keep unconditional role, authorization and security boundaries in the startup rules.
  5. Resolve the reference within its owning installed plugin.
  6. Missing or unreadable content stops the affected action.
  7. Read references explicitly when triggered.
  8. Never load a reference through an `@` import.
  9. Package every reference in each distribution.
  10. Retain trust-boundary review coverage.
  11. Verify complete contract retention in installed-session acceptance.
  12. Verify the required native reference read in that acceptance.
- A qualifying router may use two H2 sections and fewer than 25 lines.
- A qualifying router keeps one whole-contract read gate instead of repeating the moved carve-out catalog.
- Every other rule follows the reference's Structure clause unchanged.
