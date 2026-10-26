---
alwaysApply: false
applyTo: "skills/**, scripts/**, skills/**/*.sh, skills/**/*.py, skills/**/SKILL.md — when choosing between a script, a bounded classifier and the model, or authoring the scripts skills invoke"
description: Deterministic operations → script, a fixed answer set read by meaning → bounded classification, everything else → LLM, the regex trap, script structure conventions, precheck gating
---

# Script Delegation

## Required Read

- Before choosing between a script, bounded classification and reasoning, or authoring scripts and their invocation, read `skills/coding-contracts/references/script-delegation.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Deterministic logic uses executable scripts; bounded classification needs its approved bounds; reasoning stays with the model.
