# Release Runbook for a Multi-Change Sprint

## Problem/Feature Description

A small open-source library team is wrapping up a sprint that produced three distinct categories of changes that need to ship as separate PRs. The team is inconsistent about when to bump versions manually versus letting their CI pipeline handle it, and keeps picking the wrong target version number when a bump is needed. The team lead wants a `RELEASE_RUNBOOK.md` that documents the versioning decision and the release sequencing for each type of change.

## Output Specification

Produce a `RELEASE_RUNBOOK.md` file that covers, for each of the three change types below:
- Pre-PR readiness steps (what must be verified before creating the PR)
- Whether the project manifest's version field should be updated, and if so, to what target version number (the current version is in the manifest fixture below)
- A reasoned recommendation for the order in which the three PRs should ship, given the relative risk and downstream impact

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
