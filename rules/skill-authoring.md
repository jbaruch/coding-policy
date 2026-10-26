---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md, .tessl-plugin/plugin.json, agent-plugin.yaml — when authoring or modifying skills or their manifests"
description: SKILL.md structure, frontmatter, execution-mode preamble, flat step numbering, typed Skill() calls, plugin.json reference
---

# Skill Authoring

## Required Read

- Before authoring or reviewing skills, references and manifests, or relying on a skill invocation or lookup exception, read `skills/coding-contracts/references/skill-authoring.md` in full.
- Its full text and named sections bind as rule content.
- Resolve the reference within its owning installed plugin.
- Missing or unreadable content stops the affected action until the plugin is repaired.
- Read explicitly when triggered.
- Never `@`-import the reference.

## Boundary

- Keep SKILL.md an ordered execution plan.
- Use typed cross-skill calls.
- Use the declared script lookup contract.
