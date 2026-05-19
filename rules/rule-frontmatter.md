---
alwaysApply: false
applyTo: "rules/**/*.md, tile.json — when authoring rule files"
description: Frontmatter conventions for rule files — what to set for always-on vs conditional rules
---

# Rule Frontmatter

## Passthrough

- Tessl preserves rule file frontmatter byte-identical from publish to install — no per-agent translation, no field filtering
- Whatever frontmatter the source rule contains reaches every consuming agent's context
- The agent's model reads the frontmatter and applies the rule according to its declared scope
- Verified: a published rule scoped to TypeScript fires on TS files and skips Python files

## Always-On Rules

- For rules that apply regardless of file or task context (commits, secrets, code formatting, error handling): set `alwaysApply: true` in both `tile.json` steering and the rule file frontmatter
- Omit `applyTo:` — the rule has no narrowed scope
- Include `description:` for the agent UI and human readers

## Conditional Rules

- For rules whose prescriptions only fire in specific files or contexts: set `alwaysApply: false` in both `tile.json` steering and the rule file frontmatter, and add `applyTo:`
- `applyTo:` syntax: glob patterns plus an optional natural-language clause — `applyTo: "skills/**/SKILL.md — when authoring or modifying skills"`
- Field aliases the model also reads: `globs:`, `paths:`, scope-prose in `description:` — pick one form per file, do not mix

## When to Path-Scope

- Path-scope when **every** prescription in the rule is bound to a specific file set or context (tile-authoring rules whose content only fires inside `rules/`, `skills/`, `evals/` are good candidates)
- Stay universal when the rule mixes file-bound and broad guidance — `applyTo:` is exclusionary at the model layer, so a too-narrow scope drops the broad bits
- Example: `dependency-management` covers "stdlib first" (fires when writing code) and "pin versions" (fires when editing manifests); scoping to manifests would silently drop the code-level guidance

## tile.json and Rule File in Agreement

- Set the same `alwaysApply` value in both `tile.json`'s steering entry and the rule file frontmatter — split values are inconsistent and may produce surprising behavior across consuming agents
- `applyTo:` lives only in the rule file (not in `tile.json`)
- Install-time write map per agent — see `docs/tessl-rule-frontmatter.md` (draft for `docs.tessl.io`)
