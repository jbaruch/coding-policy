---
alwaysApply: false
applyTo: "rules/**/*.md — when authoring rule files"
description: Frontmatter conventions for rule files — what to set for always-on vs conditional rules
---

# Rule Frontmatter

## Required Read

- Before authoring, interpreting or changing rule frontmatter and activation scope, read `skills/coding-contracts/references/rule-frontmatter.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Frontmatter narrows applicability at the model layer; Tessl still imports the complete declared rule bytes.
