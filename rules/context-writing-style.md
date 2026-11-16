---
alwaysApply: false
applyTo: "rules/**/*.md, skills/**/SKILL.md, README.md — when writing prose in plugin context artifacts"
description: Prose discipline for rules, skills, and READMEs — what to cut, what to keep, structural format
---

# Context Writing Style

## Required Read

- Before writing plugin prose or its PR descriptions, judging policy edge cases, debugging directives or auditing rules, or relying on any exception to this rule, read `skills/coding-contracts/references/context-writing-style.md` in full.

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
  5. Gate the reference through a Required Read section that `rules/required-read.md` governs.
  6. Package every reference in each distribution.
  7. Retain trust-boundary review coverage.
  8. Verify complete contract retention in installed-session acceptance.
  9. Verify the required native reference read in that acceptance.
- A qualifying router may use two H2 sections and fewer than 25 lines.
- A qualifying router keeps one whole-contract read gate instead of repeating the moved carve-out catalog.
- A qualifying router's Required Read never restates `rules/required-read.md`.
- Every other rule follows the reference's Structure clause unchanged.
