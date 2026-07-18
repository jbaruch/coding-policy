---
name: migrate-to-plugin
description: >
  Migrate a legacy tile.json plugin to the .tessl-plugin/plugin.json form.
  Runs `tessl plugin migrate`, renames .tileignore to .tesslignore, removes
  the obsolete tile.json, re-lints, then reconciles residual "tile" wording
  to "plugin" in the repo's prose.
  Use when a repo still has a tile.json (and no .tessl-plugin/plugin.json),
  or when the user asks to migrate / convert / modernize a tile.json plugin,
  move off tile.json, or adopt the plugin.json manifest form.
---

# Migrate to Plugin Skill

Process steps in order. Do not skip ahead.

Convert a legacy `tile.json` plugin to the `plugin.json` form and reconcile the leftover terminology. The deterministic mechanics live in a script; the prose reconciliation in Step 2 is the judgment this skill owns.

## Step 1 — Run the Migration Script

Run the deterministic migration from the repo root:

```bash
.tessl/plugins/jbaruch/coding-policy/skills/migrate-to-plugin/migrate.sh .
```

The script detects the manifest state, runs `tessl plugin migrate`, renames `.tileignore` → `.tesslignore`, removes the obsolete `tile.json`, runs `tessl plugin lint`, and emits a JSON report with a `residual_files` list. Contract — inputs, output shape, exit codes — is in the script's top-of-file docstring.

A non-zero exit with no JSON on stdout (exit 2) is a tool or precondition failure — jq/tessl missing, the path is not a directory, `tessl plugin migrate` produced no manifest, or the residual-file scan could not read the tree. Surface the script's stderr diagnostic and stop; do not proceed.

Otherwise parse stdout and branch on the emitted `status`:

- `not-a-plugin` (exit 0) — no `tile.json` or `plugin.json` here. Report that this is not a tessl plugin directory and finish here.
- `already-migrated` (exit 0) — `plugin.json` already exists, nothing to convert. If `residual_tile_refs` is 0, finish here; otherwise proceed to Step 2 to reconcile the leftover wording.
- `migrated` with `lint_ok: false` (exit 1) — migration ran but `tessl plugin lint` failed. Report the lint failure, run `tessl plugin lint` directly to read it, fix the manifest, then proceed to Step 2.
- `migrated` with `lint_ok: true` (exit 0) — proceed immediately to Step 2.

## Step 2 — Reconcile Residual Terminology

Read each path in the script's `residual_files`. For every "tile"/"tiles" occurrence, decide rename-vs-keep — the script counts the pattern but makes no judgment.

Rename to plugin wording:

- Package-sense prose — "the tile", "every other tile", "tile-prescribed", "20 steering rules" → "the plugin", "every other plugin", "plugin-prescribed", "20 rules"
- `tessl tile <cmd>` → `tessl plugin <cmd>` (the canonical alias)

Keep "tile" unchanged — these are live contracts, not prose:

- The `tile.json` filename in historical or legacy-format references
- `v1/tiles/...` REST API routes and any argument bound to that route
- Code identifiers — variable, function, type, and class names
- Real external documentation page paths and analytics property names

If `residual_files` is empty, proceed silently to Step 3.

## Step 3 — Sync Affected Surfaces

Apply surface sync per `rules/context-artifacts.md` for any prose touched in Step 2:

- Update the repo's `README.md` rules/skills tables if their wording changed
- Add a `CHANGELOG.md` entry describing the migration, matching the repo's release model per `rules/context-artifacts.md` CHANGELOG Hygiene

If Step 2 changed no surfaces, proceed silently. Proceed immediately to Step 4.

## Step 4 — Verify the Result

Re-run `tessl plugin lint` and confirm it passes. Report the final state: manifest migrated, `.tesslignore` renamed, `tile.json` removed, and the files whose wording was reconciled. Finish here.
