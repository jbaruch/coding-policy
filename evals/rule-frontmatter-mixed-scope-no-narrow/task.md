# Add a Stdlib-First Rule to a Tessl Plugin

## Problem/Feature Description

A small team maintains a Tessl plugin that ships coding rules to their AI agents. They want to add a new rule that captures two related practices their senior engineers keep reinforcing in code review:

1. When writing code, prefer the standard library over adding a third-party dependency. If a standard-library solution covers the case, use it.
2. When a third-party dependency genuinely is needed and gets added, it should be pinned to a specific version in the project's manifest rather than left to float.

Both halves live in one rule because the engineers want them surfaced together — the "prefer stdlib" practice is what prevents most dependency additions from happening in the first place, and the "pin it if you add it" practice handles the cases that get through. Add the rule to the existing plugin.

## Output Specification

Produce:
- A new `rules/stdlib-first.md` rule file with the rule body covering both practices
- An updated `.tessl-plugin/plugin.json` that ships the new rule

## Input Files

The following files are the current state of the plugin. Extract them before beginning.

=============== FILE: inputs/.tessl-plugin/plugin.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.4.0",
  "description": "Acme's coding rules for AI agents",
  "private": false,
  "rules": [
    "rules/commit-conventions.md",
    "rules/spaces-not-tabs.md",
    "rules/pin-dependencies.md"
  ]
}

=============== FILE: inputs/rules/pin-dependencies.md ===============
---
alwaysApply: false
applyTo: "**/package.json, **/pyproject.toml, **/Cargo.toml, **/go.mod — when editing dependency manifests"
---

# Pin Dependencies

- Pin every third-party dependency to a specific version
- Avoid floating ranges and wildcards
- Lock files are committed alongside the manifest
