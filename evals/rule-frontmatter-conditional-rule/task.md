# Add a Dependency-Pinning Rule to a Tessl Tile

## Problem/Feature Description

A small team maintains a Tessl tile that ships coding rules to their AI agents. They want to add a new rule that requires dependency versions to be pinned (no floating ranges, no wildcard versions). The rule is only relevant when contributors are working with the project's dependency configuration — adding a new dependency, updating an existing one, or removing one. Outside of that context the rule has nothing to say. Add the rule to the existing tile so it surfaces to agents when contributors touch dependency configuration but stays out of the way otherwise.

## Output Specification

Produce:
- A new `rules/pin-dependencies.md` rule file with the rule body
- An updated `tile.json` reflecting the new rule's presence in the steering map

## Input Files

The following files are the current state of the tile. Extract them before beginning.

=============== FILE: inputs/tile.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.3.0",
  "summary": "Acme's coding rules for AI agents",
  "entrypoint": "README.md",
  "steering": {
    "commit-conventions": {
      "rules": "rules/commit-conventions.md",
      "alwaysApply": true
    },
    "spaces-not-tabs": {
      "rules": "rules/spaces-not-tabs.md",
      "alwaysApply": true
    }
  }
}

=============== FILE: inputs/rules/spaces-not-tabs.md ===============
---
alwaysApply: true
---

# Spaces Not Tabs

- Use spaces for indentation, not tabs
- Indent width follows the project's editor configuration
