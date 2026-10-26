---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md, .tessl-plugin/plugin.json, agent-plugin.yaml — when authoring or modifying skills or their manifests"
description: SKILL.md structure, frontmatter, execution-mode preamble, flat step numbering, typed Skill() calls, plugin.json reference
---

# Skill Authoring

## Required Read

- Before authoring, modifying or reviewing a skill, its references or a plugin manifest, read `skills/coding-contracts/references/skill-authoring.md` in full.
- Its full text and named sections bind as rule content. Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered; never `@`-import the reference.

## Boundary

- Keep SKILL.md an ordered execution plan; use typed cross-skill calls and the declared script lookup contract.
