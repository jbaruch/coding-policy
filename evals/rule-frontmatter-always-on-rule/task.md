# Add a Code-Style Rule to a Tessl Tile

## Problem/Feature Description

A small team maintains a Tessl tile that ships coding rules to their AI agents. They want to add a new rule that requires every code file in any project to use spaces (never tabs) for indentation. The practice applies regardless of language, framework, or file type — it's a baseline conformance the team wants enforced everywhere. Add the rule to the existing tile.

## Output Specification

Produce:
- A new `rules/spaces-not-tabs.md` rule file with the rule body
- An updated `tile.json` reflecting the new rule's presence in the steering map

## Input Files

The following files are the current state of the tile. Extract them before beginning.

=============== FILE: inputs/tile.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.2.0",
  "summary": "Acme's coding rules for AI agents",
  "entrypoint": "README.md",
  "steering": {
    "manifest-pin": {
      "rules": "rules/manifest-pin.md",
      "alwaysApply": false
    },
    "test-fixture-hygiene": {
      "rules": "rules/test-fixture-hygiene.md",
      "alwaysApply": false
    }
  }
}

=============== FILE: inputs/rules/manifest-pin.md ===============
---
alwaysApply: false
applyTo: "**/package.json, **/pyproject.toml, **/Cargo.toml, **/go.mod — when editing dependency manifests"
---

# Manifest Pin

- Pin every third-party dependency to a specific version
- Avoid floating ranges and wildcards
- Lock files are committed alongside the manifest

=============== FILE: inputs/rules/test-fixture-hygiene.md ===============
---
alwaysApply: false
applyTo: "**/tests/**, **/*_test.*, **/test_*.* — when authoring or modifying tests"
---

# Test Fixture Hygiene

- Tests must be deterministic — no self-generated random data
- Build test data programmatically in fixtures
- Clean up after yourself: temp files, database state, mock patches
