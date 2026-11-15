---
alwaysApply: false
applyTo: "** — when authoring rule frontmatter or assembling an agent's context from rule files"
description: Frontmatter conventions for rule files — always-on vs conditional rules, and how hosts filter conditional rules
---

# Rule Frontmatter

## Required Read

- Before authoring, interpreting or changing rule frontmatter and activation scope, filtering rules from an agent's context, or relying on any exception to this rule, read `skills/coding-contracts/references/rule-frontmatter.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- A host may omit a conditional rule from an agent's context.
- Tiers run cheapest first, host script, then bounded classifier, then reasoning model.
- Each undecided case fails safe to inclusion.
- An undecided rule may load on demand from its path and verbatim frontmatter.
- Included rules keep their bytes and frontmatter unchanged.
