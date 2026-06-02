# Convert a Universal Rule to a Conditional Rule

## Problem/Feature Description

A small team maintains a Tessl plugin that ships coding rules to their AI agents. One of their existing rules, `pr-template-checklist`, is currently marked to apply across every context. After several months of using it, the team realizes the rule only meaningfully fires when contributors are authoring or editing pull-request-related artifacts — PR description templates, contributor guides, the PR body itself when reviewing a draft. Outside of that context the rule has nothing to say, and surfacing it in every conversation is noise. Convert the rule from a universal rule into a conditional rule scoped to PR-related artifacts.

## Output Specification

Modify `rules/pr-template-checklist.md` — update its frontmatter so the rule fires only on PR-related artifacts. Leave the rule body content unchanged.

## Input Files

The following files are the current state of the plugin. Extract them before beginning.

=============== FILE: inputs/.tessl-plugin/plugin.json ===============
{
  "name": "acme/coding-rules",
  "version": "0.5.0",
  "description": "Acme's coding rules for AI agents",
  "private": false,
  "rules": [
    "rules/commit-conventions.md",
    "rules/pr-template-checklist.md"
  ]
}

=============== FILE: inputs/rules/pr-template-checklist.md ===============
---
alwaysApply: true
---

# PR Template Checklist

- Every PR template includes a "summary" section and a "test plan" section
- The summary names the change and the motivation
- The test plan lists verification steps the reviewer can execute
- Templates live in `.github/PULL_REQUEST_TEMPLATE.md` or `.github/PULL_REQUEST_TEMPLATE/*.md`
