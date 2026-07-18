---
alwaysApply: false
applyTo: "rules/**/*.md — when authoring rule files"
description: Frontmatter conventions for rule files — what to set for always-on vs conditional rules
---

# Rule Frontmatter

## Passthrough

- Tessl preserves rule file frontmatter byte-identical from publish to install — no per-agent translation, no field filtering
- Whatever frontmatter the source rule contains reaches every consuming agent's context
- The agent's model reads the frontmatter and applies the rule according to its declared scope
- Verified: a published rule scoped to TypeScript fires on TS files and skips Python files

## Always-On Rules

- For rules that apply regardless of file or task context (commits, secrets, code formatting, error handling): set `alwaysApply: true` in the rule file frontmatter
- Omit `applyTo:` — the rule has no narrowed scope
- `description:` is optional for always-on rules; add it when the README rules-table row needs a complement, otherwise the rule body's H1 + sections speak for themselves

## Conditional Rules

- For rules whose prescriptions only fire in specific files or contexts: set `alwaysApply: false` in the rule file frontmatter and add `applyTo:`
- `applyTo:` syntax is `"<glob list> — <natural-language clause>"` — both halves required. Example: `applyTo: "skills/**/SKILL.md — when authoring or modifying skills"`
- The em dash separates the file-level scope (globs) from the action-level scope (prose); the model reads both halves and narrows on whichever signal is clearer
- `globs:` and `paths:` are accepted **field-name aliases** for `applyTo:` — the value still follows the em-dash glob+prose form; pick one field name per file
- `description:` is a rule summary, not a scoping mechanism — never put scope prose only in `description:` as a substitute for `applyTo:` on a conditional rule

## When to Path-Scope

- Path-scope when **every** prescription in the rule is bound to a specific file set or context (plugin-authoring rules whose content only fires inside `rules/`, `skills/` are good candidates)
- Stay universal when the rule mixes file-bound and broad guidance — `applyTo:` is exclusionary at the model layer, so a too-narrow scope drops the broad bits
- Example — `dependency-management` covers two practices:
  - "stdlib first" fires when writing code
  - "pin versions" fires when editing manifests
- Scoping `dependency-management` to manifests would silently drop the code-level guidance, so the rule stays universal

## Frontmatter Is the Only Scope Surface

- `alwaysApply` and `applyTo:` live only in the rule file frontmatter — `.tessl-plugin/plugin.json` lists rule paths in its `rules` array and carries no per-rule config
- Install-time write map per agent — `docs/tessl-rule-frontmatter.md` is the repo-internal draft of additions for `docs.tessl.io`. The `docs/` directory is `.tesslignore`d and does not ship with the published plugin, so consumers read the integrated content on `docs.tessl.io` once it lands; until then, read the source on GitHub
