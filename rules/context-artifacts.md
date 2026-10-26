---
alwaysApply: false
applyTo: ".tessl-plugin/plugin.json, agent-plugin.yaml, rules/**, skills/**, .tesslignore, CHANGELOG.md, README.md — when authoring or modifying plugin artifacts; the Tessl manifest, lint, review and publication provisions bind Tessl plugin artifacts only"
description: Plugin structure, rule/skill format, review pipeline, surface sync, consistency audits — the authoring contract for plugin artifacts, Tessl-specific provisions scoped to Tessl distribution
---

# Context Artifacts

## Required Read

- Before authoring, modifying, reviewing or publishing plugin artifacts in any distribution, or relying on any exception to this rule, read `skills/coding-contracts/references/context-artifacts.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Keep plugin surfaces synchronized.
- Validate and review every applicable distribution before publication.
