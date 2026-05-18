---
alwaysApply: true
---

# Stateful Artifacts

## What Counts

- JSON (or similar) state files a skill writes and/or reads across invocations to maintain continuity between runs
- Distinct from the tile's static context artifacts (rules, skills, scripts per `rules/context-artifacts.md`) — those don't change between runs; these do
- Lifecycle and packaging expectations come from `rules/context-artifacts.md`; this rule adds the stateful-specific requirements below

## Required Attributes

- A **schema** documented next to the owner skill (e.g., `skills/<name>/state-schema.md` or a JSON Schema file); no schema, no artifact
- A single **owner skill** responsible for shape changes — shared ownership means no one owns the migration
- A `schema_version` field on every record so migrations are auditable
- A **writer / reader contract** — which skills write, which read, what each promises about field presence, defaults, and format

## Hints, Not Authority

- Artifacts are last-seen snapshots, not ground truth — before acting on a recalled value, verify against the live source (API, DB, filesystem)
- Stale state is the default; assume it until proven fresh

## Migration Policy

- Bump `schema_version` for any shape change — don't repurpose a field silently
- Only the owner skill migrates: on its own read, detect old `schema_version`, upgrade the record, rewrite
- Reader skills (non-owners) must not migrate — on encountering an old version, treat it as "no usable prior state" (read-only) and let the next owner-skill run perform the upgrade

## Rename / Removal

- Renaming or removing an artifact follows surface sync (see `rules/context-artifacts.md`): update the owner skill's schema doc, update every reader skill, add a CHANGELOG entry
- Keep reader skills tolerant of missing artifacts — an artifact that was never written (first run) is indistinguishable from one that was removed; both mean "no prior state"
