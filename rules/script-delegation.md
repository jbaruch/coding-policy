---
alwaysApply: false
applyTo: "skills/**, scripts/**, skills/**/*.sh, skills/**/*.py, skills/**/SKILL.md — when choosing between a script, a bounded classifier and the model, or authoring the scripts skills invoke"
description: Deterministic operations → script, a fixed answer set read by meaning → bounded classification, everything else → LLM, the regex trap, script structure conventions, precheck gating
---

# Script Delegation

## Required Read

- Before choosing, authoring or invoking scripts, bounded classifiers and automation, or choosing model reasoning, read `skills/coding-contracts/references/script-delegation.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Deterministic logic uses executable scripts.
- Bounded classification needs its approved bounds.
- Reasoning stays with the model.
- Classification approval waives no unrelated gate.
- It grants no authority for third-party communication, data deletion or merge.
