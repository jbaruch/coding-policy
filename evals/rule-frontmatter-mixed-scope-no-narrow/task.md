# Add a Stdlib-First Rule to a Tessl Tile

## Problem/Feature Description

A small team maintains a Tessl tile that ships coding rules to their AI agents. They want to add a new rule that captures two related practices their senior engineers keep reinforcing in code review:

1. When writing code, prefer the standard library over adding a third-party dependency. If a standard-library solution covers the case, use it.
2. When a third-party dependency genuinely is needed and gets added, it should be pinned to a specific version in the project's manifest rather than left to float.

Both halves live in one rule because the engineers want them surfaced together — the "prefer stdlib" practice is what prevents most dependency additions from happening in the first place, and the "pin it if you add it" practice handles the cases that get through. Add the rule to the existing tile.

## Output Specification

Produce:
- A new `rules/stdlib-first.md` rule file with the rule body covering both practices
- An updated `tile.json` reflecting the new rule's presence in the steering map

## Input Files

The following files are the current state of the tile. Extract them before beginning.

=============== FILE: inputs/tile.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.4.0",
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
    },
    "pin-dependencies": {
      "rules": "rules/pin-dependencies.md",
      "alwaysApply": false
    }
  }
}
