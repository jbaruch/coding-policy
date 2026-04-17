# Release Runbook for a Multi-Change Sprint

## Problem/Feature Description

A small open-source library team is wrapping up a sprint that produced three distinct categories of changes that need to ship as separate PRs. The team is inconsistent about when to bump versions manually versus letting their CI pipeline handle it, and junior contributors often open PRs with vague titles and skip steps before requesting review. The team lead wants a `RELEASE_RUNBOOK.md` that documents the exact steps for releasing each type of change, so anyone on the team can follow it reliably.

The runbook should also explain how to handle feedback left by automated code reviewers — including both cases where a suggestion is accepted and where it should be politely declined — so that PRs don't stall waiting for replies.

## Output Specification

Produce a `RELEASE_RUNBOOK.md` file that covers the complete release process for the three change types listed below. For each type, the runbook should describe:
- Pre-PR readiness steps
- How to construct the PR title and body
- What to do about versioning (whether and how to update the project manifest)
- How to handle automated review feedback before merging

You may also produce a `release.sh` helper script if you find it useful to illustrate the steps programmatically.

## Input Files

The following files represent the three pending changes to release. Extract them before beginning.

=============== FILE: inputs/changes.md ===============
# Pending Changes

## Change A — Fix null pointer in user lookup
- **File modified**: `src/users/lookup.js`
- **What it does**: Guards against a null return value from the database adapter that was causing crashes in production
- **Backward compatible**: Yes
- **New public API**: No

## Change B — Add CSV export endpoint
- **File modified**: `src/export/csv.js` (new file), `src/routes/index.js`
- **What it does**: Adds a new `/export/csv` route that lets users download their data
- **Backward compatible**: Yes (additive)
- **New public API**: Yes — new route and response format

## Change C — Remove deprecated `v1` API routes
- **File modified**: `src/routes/v1/` (deleted), `src/routes/index.js`
- **What it does**: Drops all `/v1/*` endpoints that were deprecated 6 months ago
- **Backward compatible**: No — existing clients using `/v1/` will break
- **New public API**: No

=============== FILE: inputs/package.json ===============
{
  "name": "data-service",
  "version": "1.4.2",
  "description": "A small data management library",
  "main": "src/index.js"
}
