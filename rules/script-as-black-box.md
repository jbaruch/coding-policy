---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md — when authoring skills or reference files that invoke scripts"
description: Skills reference the script's contract (inputs, outputs, exit codes, emitted shapes), not its internal logic — thresholds, predicates, formulas, and allowlists live in the script
---

# Script as Black Box

## Required Read

- Before authoring skills or references that invoke scripts, or relying on any exception to this rule, read `skills/coding-contracts/references/script-as-black-box.md` in full.

## Boundary

- Describe inputs, outputs, failures and side effects.
- The script owns internal thresholds, predicates and allowlists.
