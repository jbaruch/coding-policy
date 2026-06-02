# Add a Code-Style Rule to a Tessl Plugin

## Problem/Feature Description

A small team maintains a Tessl plugin that ships coding rules to their AI agents. They want to add a new rule that requires every code file in any project to use spaces (never tabs) for indentation. The practice applies regardless of language, framework, or file type — it's a baseline conformance the team wants enforced everywhere. Add the rule to the existing plugin.

## Output Specification

Produce:
- A new `rules/spaces-not-tabs.md` rule file with the rule body
- An updated `.tessl-plugin/plugin.json` that ships the new rule

## Input Files

The following files are the current state of the plugin. Extract them before beginning.

=============== FILE: inputs/.tessl-plugin/plugin.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.2.0",
  "description": "Acme's coding rules for AI agents",
  "private": false,
  "rules": [
    "rules/manifest-pin.md",
    "rules/test-fixture-hygiene.md"
  ]
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
