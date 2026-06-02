# Add a Dependency-Pinning Rule to a Tessl Plugin

## Problem/Feature Description

A small team maintains a Tessl plugin that ships coding rules to their AI agents. They want to add a new rule that requires dependency versions to be pinned (no floating ranges, no wildcard versions). The rule is only relevant when contributors are working with the project's dependency configuration — adding a new dependency, updating an existing one, or removing one. Outside of that context the rule has nothing to say. Add the rule to the existing plugin so it surfaces to agents when contributors touch dependency configuration but stays out of the way otherwise.

## Output Specification

Produce:
- A new `rules/pin-dependencies.md` rule file with the rule body
- An updated `.tessl-plugin/plugin.json` that ships the new rule

## Input Files

The following files are the current state of the plugin. Extract them before beginning.

=============== FILE: inputs/.tessl-plugin/plugin.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.3.0",
  "description": "Acme's coding rules for AI agents",
  "private": false,
  "rules": [
    "rules/commit-conventions.md",
    "rules/spaces-not-tabs.md"
  ]
}

=============== FILE: inputs/rules/spaces-not-tabs.md ===============
---
alwaysApply: true
---

# Spaces Not Tabs

- Use spaces for indentation, not tabs
- Indent width follows the project's editor configuration
