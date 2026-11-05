# Stateful Artifacts

## What Counts

- JSON (or similar) state files a skill writes and/or reads across invocations to maintain continuity between runs
- Distinct from the plugin's static context artifacts (rules, skills, scripts per `rules/context-artifacts.md`) — those don't change between runs; these do
- Lifecycle and packaging expectations come from `rules/context-artifacts.md`; this rule adds the stateful-specific requirements below

## Required Attributes

- A **schema** documented by its owning component; skill-owned schemas stay beside the owner skill
- A single **owner component** responsible for shape changes: a skill, runtime or service
- A runtime-owned database table's versioned migrations are its schema of record
- Skill readers of runtime-owned tables cite an installed contract naming those migrations and the reader contract
- A `schema_version` field on every record so migrations are auditable
- A **writer / reader contract** — which components write, which read, what each promises about field presence, defaults, and format

## Read-Only Legacy Runtime Tables

- Narrow exception for an existing runtime table whose owner initializes and upgrades its schema inline, outside versioned migrations.
- Applies only to read-only consumers of tables that already exist before the consuming change. It permits the existing inline schema in place of versioned migrations and per-record `schema_version`.
- Preconditions (all required):
  1. An installed contract names the finite table inventory, runtime owner, actual initializer and inline upgrades, and fields the reader requires. Cite the real schema of record; invent no migration or version guarantee.
  2. The reader uses the authorized native source and its declared identity/scope checks. It never initializes, migrates, backfills or substitutes a database.
  3. Missing required schema or a query failure produces an actionable unavailable result. It never means an empty successful read.
  4. This exception adds no store or writer and authorizes no schema change. New tables, new persistent artifacts and owner schema changes retain the ordinary versioning requirements.
- Every other artifact follows Required Attributes and Migration Policy unchanged.

## Append-Only Runtime Telemetry

- Narrow exception for an existing append-only runtime telemetry log.
- Applies to historical observations, never continuity state or action gates.
- Preconditions (all required):
  1. An installed reader contract names the finite artifact inventory, real runtime owner, owner's schema-of-record source, record kinds, schemas, writers and readers. Cite the actual owner source; invent no provider, schema or version guarantee.
  2. Every new record carries `schema_version`. The owner declares existing unversioned records as legacy version 0 and documents accepted versions, required fields and defaults.
  3. Readers preserve the meaning of accepted historical records and accept only documented kinds and versions. Unsupported versions produce actionable unavailable evidence, never invented zeros, health or permission.
  4. Historical records remain unchanged. Readers neither migrate, rewrite nor backfill the log; the owner emits the current version on new appends.
  5. The log supplies no authorization, payment, deduplication, recovery or other action gate. A change that repurposes historical field meaning does not qualify.
- Every other artifact follows Required Attributes and Migration Policy unchanged.

## Hints, Not Authority

- Artifacts are last-seen snapshots, not ground truth — before acting on a recalled value, verify against the live source (API, DB, filesystem)
- Stale state is the default; assume it until proven fresh

## Migration Policy

- Bump `schema_version` for any shape change — don't repurpose a field silently
- Only the owner migrates: detect accepted older versions, upgrade and rewrite through its documented migration path
- Non-owner readers must not migrate
- On an older record, a reader treats it as read-only "no usable prior state" — the next owner run upgrades it
- On a record newer than it accepts, a reader is lagging, not awaiting migration — treat it as "no usable prior state" and update the reader to accept the new version (see Cross-Pipeline Schema Bumps when writer and readers deploy through separate pipelines)
- Narrow exception for a gate store.
- Applies when an open record in the artifact refuses an action, so "no usable prior state" reads as no gate
- Preconditions (all required):
  1. The owner module's schema documentation names the artifact and this exception
  2. A reader meeting a version it does not accept refuses with an actionable diagnostic, never proceeding as if no record exists
  3. The owner migrates and rewrites every older version it accepts, on read
- Every other artifact's lagging reader treats a newer record as "no usable prior state"

## Rename / Removal

- Renaming or removing an artifact follows surface sync (see `rules/context-artifacts.md`): update the owner's schema doc, update every reader, add a CHANGELOG entry
- Keep reader skills tolerant of missing artifacts — an artifact that was never written (first run) is indistinguishable from one that was removed; both mean "no prior state"

## Cross-Pipeline Schema Bumps

- Applies when the writer and its readers deploy through separate pipelines: different repos, registries, or release cadences
- Treat the bump as non-atomic — production runs mixed versions for the rollout window
- A single-exact-version reader gate takes its no-prior-state path (see Migration Policy) only for records stamped with a version it doesn't accept — matching-version records still read normally
- That no-prior-state fallback MUST be safe and non-disruptive — never a path that escalates work (wake-always, full recompute, alert storm)
- A zero-skew rollout requires readers that parse both old and new shapes for the window: deploy those dual-accept readers (read-only, never migrating) first → flip the writer → drop the old version from the readers' accepted set
- Additive (backward-compatible) bumps always allow dual-accept — the new reader reads old records via field defaults
- Breaking bumps allow dual-accept only when one reader can be written to parse both shapes, at higher cost
- Narrow exception for breaking bumps with no dual-parse reader
- Take the writer offline for an atomic cutover — the dual-accept sequence does not apply
- Preconditions (all required):
  1. The new shape is not readable by a reader written for the old shape (the bump is breaking)
  2. No single reader can be written to parse both shapes for the rollout window
- Every other bump — additive, or breaking with a feasible dual-parse reader — uses the zero-skew dual-accept rollout above
