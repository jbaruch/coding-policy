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
  1. Preserve the rule's applicability and affected-action trigger.
  2. Require the full reference before every affected action, including reliance on an exception.
  3. Preserve applicable obligations not removed or changed by the authorized amendment.
  4. Keep unconditional role, authorization and security boundaries in the startup rules.
  5. Gate the reference through a Required Read section that `rules/required-read.md` governs.
  6. Package references required by each distribution's shipped contract.
  7. Retain trust-boundary review coverage.
  8. For changes affecting activation, reference loading or packaging, verify complete contract retention and the required native reference read in installed-session acceptance.
- A qualifying router may use two H2 sections and fewer than 25 lines.
- A qualifying router keeps one whole-contract read gate instead of repeating the moved carve-out catalog.
- A qualifying router's Required Read never restates `rules/required-read.md`.
- Every other rule follows the reference's Structure clause unchanged.
