# Foreman State Schema

Schemas for the cross-invocation artifacts owned by `herdr-foreman`, per
`rules/stateful-artifacts.md`. The Python utility alone writes and migrates
`state.json`. The foreman maintains the separate Markdown task ledger; the utility
does not parse or update that document. The foreman drafts retrospective synthesis;
the utility alone records the saved notes and their separate index.

## Artifacts

| Path | Owner | Purpose |
| ---- | ----- | ------- |
| `$XDG_STATE_HOME/foreman/state.json` (default `~/.local/state/foreman/state.json`, override `--state FILE`) | `skills/herdr-foreman/foreman/state.py` and its `skills/herdr-foreman/foreman/recovery.py` helper, within the same owner skill | Snapshots, append-only assignments, and audited task recovery |
| `$XDG_CONFIG_HOME/foreman/config.json` (default `~/.config/foreman/config.json`, override `--config FILE`) | the operator | Per-agent usage / clear commands; foreman reads it and never writes it |
| `<task-reports-dir>/TASK-LEDGER.md` | `herdr-foreman`, written by the foreman | Evidence-backed assignment acceptance and task completion across rounds |
| `<canonical-state-path>.retrospectives/` | `herdr-foreman`, through its retrospective utility | Immutable retrospective notes, versioned index, and transition coverage |
| `<canonical-state-path>.foreman-reset.json` | `skills/herdr-foreman/foreman/foreman_reset.py` | One record per foreman round-boundary reset; see Foreman Reset Record below |

## Home Migration

Both homes were named `teamlead` before the rename to foreman (#501). Every
command whose state or config comes from the default path refuses while that
default home is still at the legacy `teamlead` path, naming `migrate-home`,
and creates nothing at the new path; a command given explicit `--state` and
`--config` is unaffected. `foreman migrate-home` is the owner's one-time move
(`skills/herdr-foreman/foreman/home.py`, module docstring):

- Every other command reading a default home holds the home guard `$XDG_STATE_HOME/.foreman-home.lock`
  shared for its whole run; `migrate-home` takes it exclusively before reading
  either home and holds it through both moves, so it refuses while any command
  runs and a command started mid-migration is refused
- It creates the state root first when only a legacy config home exists, so the guard exists to hold
- It also refuses while any owner lock in the legacy state home is held
- A failed rename or link exits with a state error naming what moved; running `migrate-home` again finishes it
- It moves the default homes only and refuses `--state` or `--config`
- It moves each home to the `foreman` path and leaves the legacy path as a
  link to it, so absolute paths quoted in history still resolve
- It rewrites each store's `state_path` identity field that names the legacy
  canonical state path
- When a retrospective `pending.json` hashes that index ancestry,
  `migrate-home` rebases `previous_index` to the rewritten identity before the
  index write. This derived digest is the only other changed JSON value
- The rebase covers committed and uncommitted journals, including a first
  record with no index. A retry accepts either side of an interrupted rebase
- Every record shape and version remains unchanged
- It refuses a home that exists at both paths, never merging them
- A second run changes nothing; a run that finds the move done but the link
  or identity fields missing finishes them

The JSON formats and utility contracts below apply to `state.json` and config.
The Markdown ledger has its own contract in Task Ledger below; adding it changes
none of the existing JSON record shapes or versions.
The retrospective sidecar also leaves `state.json` and assignment versions
unchanged. Its canonical state path is the expanded, resolved path selected by
`--state` or the existing default; separate state files have separate histories.

`skills/herdr-foreman/config.example.json` is an example to adapt and commission before live tier use. Config schema 3
adds per-agent `capabilities`; schemas 1 and 2 remain readable without rewriting
the operator-owned file. Missing capabilities mean an empty list, never inferred
expertise. Capability entries are unique lowercase identifiers validated by
`skills/herdr-foreman/foreman/config.py` (`parse_capabilities`). Declare them from available skills,
tools and inspected evidence. The example leaves every capability list empty.

Config schema 4 requires every tier table to carry a `consultation` row, the
round investigator and advisor default to (`skills/herdr-foreman/foreman/config.py`). A tier table
under schema 2 or 3 has no such row and keeps the defaults it was written
against: investigator on `reconciliation`, advisor on `architect`. Moving to
schema 4 is the operator adding the example's `consultation` row and bumping
`schema_version`; nothing rewrites the file.

Config schema 5 requires a tier table on every worker except the pinned judge
named in the `judge` block (`skills/herdr-foreman/foreman/config.py`). A worker without one gets
no tier from selection, so every round it takes records `tier: null` and runs
at whatever model is already live, unproven (#476). Under schema 4 and below
an untiered worker still loads, with that behaviour.
Config schema 6 adds the optional top-level `foreman` block
(`parse_foreman` in `skills/herdr-foreman/foreman/config.py`). The operator owns
the file and is its only writer; the utility reads it and never migrates it.
Schemas 1–5 stay readable: they carry no `foreman` block, and the seat reads as
unconfigured, the preflight warning that never blocks. A `foreman` block in a
file below schema 6 is refused with the version it needs. Moving to schema 6 is
the operator adding the block and bumping `schema_version`.
Config schema 2 added per-agent `tiers` and `launch_args`.
See `skills/herdr-foreman/references/model-tiers.md` for billing evidence. A missing config is refused with the exact `cp` command to run. The
optional `idle_markers` / `working_markers` per-agent keys carry the footer
signatures the stale-state probe reads; an agent with neither is never probed.
`slash_delivery` picks how that worker's slash commands go out, `paste` or
`type`; `dialog_next_tab_keys` names the keys that cycle a usage dialog's tabs;
`composer_glyph`, `composer_ignore_dim`, `composer_placeholders`, and
`recover_keys` drive the consumed-command check, the ghost-text and placeholder
exemptions, and the guarded one-shot recovery of a stuck composer.
`model_label` is cosmetic: it names the model on the worker's pane after a
dispatch. All are documented in
`skills/herdr-foreman/references/herdr.md`.

`window_group` names the usage window an agent shares with other agents: two
workers authenticating as one subscription declare the same value, `measure`
copies it onto each record (snapshot `schema_version` 3), and `plan` charges a seat's cost against every
worker in that window. An agent that declares none has a window to itself.

The optional top-level `judge` key pins the judge agent, model, and effort.
Plan schema 6 echoes them in a `judge` object, with `mode` — the seat's
declared `adjudication` or `diagnosis` — beside them; a plan without that seat
omits the object. Writer: `plan`, from its own `--judge-mode`. Readers:
`start-judge` and `apply`, which use the recorded mode and refuse a supplied
one that differs from it. A version-5 plan carries no `mode`; its readers
refuse the start rather than defaulting one, since the choice decides which
pre-dispatch gate the seat is held to. Model and effort become explicit launch flags. Legacy `banner_pattern`
values are ignored: proof comes from launch or live process argv. The planner
never ranks the judge seat or gives its pinned worker another role.

Plan schema 5 also carries `tiers` keyed by role and `rounds` with the foreman's
round type and context inputs. Legacy non-tiered assignments have no tier
metadata. The operator's tier table, supported flags, and billing evidence are documented in `references/model-tiers.md`.
`task_context` is null for an unlabelled plan, otherwise an object containing
`task`, cumulative `fix_round`, correction `plan` identity or null, and `work`
bounds or null. Apply refuses different task context. Earlier plan shapes and
plain role mappings remain accepted; live apply still checks current history,
allowance, tiers, and readiness. Apply output schema 7 includes
`context_transition`, persistent `dispatch_id` for labelled assignments, and
`replayed: true` when returning an existing completed result.
Version 7 adds optional per-assignment specialist `requirements` and retained
consultation handling. Version 6 added the verified role-clear transition. Version 5 adds verified hand-release and historical-correction transition
variants; version 4 introduced the original recovery fields.

Plan schema 5 adds an optional `requirements` map keyed by assigned responsibility.
Each value has `specialty`, nonempty `required_capabilities`, boolean `independent`,
and stable `engagement`. The input envelope to `plan --requirements` is
`{"schema_version": 1, "assignments": {"<role>": "<requirement object>"}}`.
The plan stores normalized requirement objects directly, without that envelope.
Absent requirements preserve legacy planning. New consultation responsibilities
require explicit requirements; the parser and selection contract live in
`references/specialists.md`. Apply rechecks current eligibility before an unsent
dispatch. A completed exact retry returns its original receipt.

Plan schema 11 adds `partition_proof` to a partitioned plan, copied unchanged
from the `validate-partition` result: `{"repo", "base", "head"}`, an absolute
repo path and the full base and head commits (SHA-1 or SHA-256) the slices were proven over
(`head` null for a working-tree proof). Writer: `plan --partition`. Reader:
`verify-partition --task`, which checks every field's shape, the
`slice_digest` and `seat_digests` against `slice_paths`, each seat's latest
dispatch for the task by event time (applied, to the plan's assigned agent,
under the plan's `task_context`, from an intact frozen brief and common brief)
against its seat digest, under the state lock, the repo, the task's recorded base,
the tip against `head`, and that the slices cover exactly
`git diff base..head` (#460, #534). A plan without the proof is refused there.
From schema 11, `slice_digest` and every `seat_digests` entry also cover
`partition_proof`, so a seat brief composed for one proven head fails the
gate for a plan proven at another, even over the same paths. `plan` takes the
proof and the slices from one read of the result.

`validate-partition` results are their own schema: version 2 carries `proof`.
`plan` refuses a version-1 result, which has none; re-validate with this
build.

Plan schema 7 adds the partitioned round's boundary: `slice_paths`, a
`{seat: [glob, ...]}` map of what each seat owns; `slice_digest` over that map;
and `seat_digests`, one digest per seat over that seat and its own globs.
Writer: `plan --partition`, from the accepted `validate-partition` result.
Readers: `compose-briefs.sh`, which renders each seat's globs and its
`seat_digests` entry into the brief without recomputing either, and `apply`,
which re-derives both from the plan and checks the seat's digest, its slice
name and every one of its globs against that seat's brief. A version-6 seated
plan carries no `seat_digests`; briefs composed from its round-level digest
fail that per-seat check and the dispatch is refused, rather than the round
digest standing in as evidence each seat's boundary was bound. An unpartitioned
plan carries none of the three keys at either version and is unaffected.

Plan schema 8 replaces the mechanical round context with one `oracle` object
in `rounds.<role>.context` (#480). Writer: `plan`, from `--round-context`.
Readers: `apply`, which recomputes each tier from that context, and
`verify-oracle`, which reads the oracle it checks from the plan rather than
from its caller. A version-7 plan carrying the retired context fields is
refused by name at apply; one without them reads unchanged, and no oracle is
ever inferred for it.

Plan schema 12 adds `oracle_pins`, a `{role: {"path", "sha256"}}` map with
one entry per `patch` or `fixture` oracle in the `rounds.<role>.context` of a
`mechanical` round (#488). Writer: `plan`, which hashes each such oracle file
as it writes the plan. Readers: `apply`, which binds each mechanical round's
oracle and pin into its dispatch (Recovery records, `dispatches`), and
`verify-oracle`, which refuses an oracle on a
round that is not `mechanical`, a `patch` or `fixture` oracle with no pin for
its role and path, an oracle file whose bytes no longer hash to the pin, and a
plan whose oracle or pin differs from the one the role's latest dispatch under
`--task` bound (#585).
A `digest` oracle carries its expected value already and takes no pin. An
older plan pinned nothing; replan it before its round is gated.

Plan schema 13 adds `selection`, one record per assigned seat explaining its
model and effort (#602). Writer: `plan`, from the same tiers, capability
table, requirements and round inputs the selection read. No reader acts on
it: `apply` ignores the key, and an older plan without it reads unchanged.
Each record carries its own `schema_version` (1) and these fields; a value the
owner cannot establish is the literal `unknown`:

| Field | Meaning |
| ----- | ------- |
| `agent`, `tiered` | The assigned worker, and whether a tier row selected its model |
| `required_capabilities` | `model`: the capability-table names the round needs; `worker`: the requirement's `required_capabilities`, empty without one |
| `model`, `effort`, `round`, `tier_row` | The selected pair, the requested round and the config row that ran it |
| `capability`, `evidence` | The tier's verdict, and per needed capability the table's `verdict` and `source`, or `unknown` with a null source |
| `cost` | `billing_window`, `effective_multiplier`, and `known` (false while the window is `unknown`) |
| `cheaper` | `floor` (`pinned_judge`, `judgment_round` or null) and `candidates`: each cheaper row the role can run, with its `verdict`, `sources`, billing window and, under a judgment floor, `barred_by_floor` |
| `escalation` | `conditions` (field, comparison, value, effect, `fired`) and `pressure` (the headroom at or below which a discretionary step is declined, null on a judgment round, and `de_escalated`) |

An untiered worker records `unknown` for every model-dependent field. The
record builder is `skills/herdr-foreman/foreman/selection.py` (`records`); the
conditions and their thresholds are `escalation_conditions` in
`skills/herdr-foreman/foreman/tiers.py`, not restated here.

Plan schema 10 adds `capability` and `cheaper_adequate` to each entry in
`tiers` (#520). Writer: `plan`, from the capability table beside the state.
`capability` is `adequate` when every capability the round needs is recorded
adequate on a supporting source, and `unknown` otherwise; an `inadequate`
entry refuses the candidate instead, so it is never stored. `cheaper_adequate`
names a cheaper configured row recorded adequate for the same needs, with the
`sources` of the entries that say so, or null, whatever the selected row's own
verdict; it is recorded, never selected, and only a row the role can run
qualifies. Reader: `apply`, which recomputes both and drops them from the tier
it dispatches, so assignment rows keep their schema. An
older plan carries neither and is refused as stale.

Plan schema 9 adds `pressure_headroom` and `de_escalated` to each entry in
`tiers` (#477). Writer: `plan`, from the measured snapshot. Reader: `apply`,
which recomputes each tier against the headroom the plan resolved with rather
than measuring again. An older plan carries neither field, reads as unmeasured
pressure, and resolves as it always did. The pinned judge's entry carries both
as null and false. `de_escalated` is true only when the declined escalation
would have changed the row, model or effort. `snapshot_ref.source` names the
snapshot by its absolute path, so `apply` finds it from any working
directory.

The optional `role_costs` key is the second:
`{"<role>": <number>}`, what one round in that seat is expected to
burn out of a worker's remaining headroom percentage. It overrides the
planner's own weights one role at a time, and a role it omits keeps the
default (see `skills/herdr-foreman/references/round-setup.md`, Step 5).
A missing map means no overrides; a value that is not a non-negative finite
number is refused, naming the file and the role. `plan` is the only reader.

## Task Ledger

Choose one absolute task reports directory outside the shared checkout and
worker worktrees. Keep `TASK-LEDGER.md` there across fixes, releases, and resumes.
Record its absolute path in the saved task authorization context and the foreman's
handoff before first dispatch. Do not move or delete it during worktree cleanup.
It replaces the informal round log, not the utility's dispatch/recovery ledger.

The field formats are named constants at the top of
`skills/herdr-foreman/foreman/members.py`, under its `Task-ledger schema 1`
comment: the schema version, the frontmatter and event field lists, the
subjects and each subject's decisions with their meanings, which fields a
task event records as not applicable, the commit-SHA pattern, and the
literals that stand in for a SHA or a report path. This section names them
and restates none of them. The Markdown document's frontmatter carries
`FRONT_FIELDS`: the stable `task`, the original `base_revision`, and the
`dispatch_state` path of the utility ledger. Each appended event is a Markdown
section carrying `EVENT_FIELDS`; an unavailable value is the `UNKNOWN`
literal, never a guess:

| Field | Meaning |
| --- | --- |
| `schema_version` | `LEDGER_SCHEMA_VERSION` on every event |
| `id`, `at` | Unique event identity and observation time, in the timezone-qualified form `chronology.timestamp` accepts |
| `subject` | Whether the event judges the whole task or one assignment |
| `dispatch_id`, `worker`, `role` | Actual utility dispatch identity and assigned worker/role; `ASSIGNMENT_IDENTITY` governs task events |
| `report` | Report path, or a `REPORT_PLACEHOLDERS` literal before it is known |
| `observed` | Source-attributed dispatch result, wait result, worker claim, or Herdr state; never an acceptance decision |
| `decision` | Foreman assessment from the event subject's `DECISION_MEANINGS` vocabulary |
| `head_revision` | Inspected commit SHA, or a `HEAD_PLACEHOLDERS` literal when unverified or without a VCS artifact |
| `evidence` | Absolute report/artifact paths with the inspected content or digest, VCS refs, and gate/run URLs with their observed results; `UNKNOWN` when none exists |
| `assessment` | Why this decision follows from the evidence, remaining criteria, and the next action |

The task identity and base in the document apply to every event. Append a new
decision when evidence changes; preserve earlier records. Event sections may
contain prose under `assessment` for the foreman's reasoning. This is a human-readable
decision log, not a new machine status API or an input to `foreman.sh apply`.

- **Writer** — the foreman running `herdr-foreman` writes after dispatch, after
  every wait outcome, after report assessment, and before any pause or handoff.
  It also records gate changes, judge decisions, release evidence, and cleanup.
  One active foreman writes a task ledger; transfer ownership explicitly on handoff.
- **Readers** — a resumed foreman and `herdr-standup` read schema 1 without changing
  its meaning. `close-member` (`skills/herdr-foreman/foreman/members.py`)
  reads it too, and only through a validated schema-1 document: every field
  present exactly once and in the format the constants above name, and
  `dispatch_state` naming an existing file that resolves to the state the
  command runs against. The validation is `ledger_events` and
  `_check_formats` in `members.py`. Any other version, a missing, repeated or
  malformed field or another state's ledger is refused and closes nothing. An append never repairs a malformed event; record a
  recovered ledger at a new path, as Migration below describes. It matches an event by `dispatch_id`, `worker` and `report`, and
  never writes the ledger. Workers never write it. Standup reads it without migration and
  labels unaccepted worker claims as reported; it grants no completion status.
- **Authority** — decisions refer to inspected evidence. Revalidate sources
  before acting on a recalled entry. The document grants no authorization,
  extra correction allowance, dispatch retry, or waiver of a gate. The utility
  remains authoritative for its recorded dispatches, counters, and recovery.
- **Missing, corrupt, or unsupported** — preserve any existing file and treat
  it as no usable prior acceptance. Reconcile utility history, live worker
  evidence, reports, VCS, and applicable external gates before continuing.
  No automatic redispatch or counter reset follows from a missing ledger.
- **Migration** — only `herdr-foreman` may migrate documented older formats,
  preserving the original entries and their evidence. Version 1 is the first
  format; an unversioned round log is evidence to assess, never automatically
  accepted history. A newer format requires an updated reader. Do not overwrite
  an unreadable or newer ledger; retain it and record a recovered ledger at a
  new disclosed path after reconciling sources. Bump the document and affected
  record versions for future shape changes.

Execution guidance and the blank event template:

```text
skills/herdr-foreman/references/task-ledger.md
```

## Retrospective Artifacts

Resolve the selected state path with `Path(...).expanduser().resolve()` and append
the literal `.retrospectives`. This directory survives task worktree and report
staging cleanup. Its owner is `herdr-foreman`; only
`skills/herdr-foreman/foreman/retrospective.py` and the owner's dispatch helpers
write it. The foreman supplies Markdown synthesis and source metadata through
`retro-record`, never edits its index or installed notes directly.

| File | Contract |
| --- | --- |
| `index.json` | Schema 1 object with canonical `state_path`, nullable `baseline_at`, append-only `records`, and recorded `transitions` |
| `<id>.md` | Immutable UTF-8 completed note with schema 1 metadata and the foreman's substantive synthesis |
| `pending.json` | Schema 1 transaction journal with `previous_index` digest and proposed `record`; removed after the index commit |
| `index.json.lock` | Utility lock; writers acquire it after the dispatch-state lock |

Each completed `records` entry carries these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `id` | Version 1; unique lowercase identifier using letters, digits, underscores, or hyphens |
| `completed_at` | Timezone-qualified completion time normalized to UTC |
| `period_start`, `period_end` | Covered interval, ordered, reaching the check receipt's `checked_at`, and ending no later than completion |
| `triggers` | `daily`, `transition`, or both |
| `tasks` | Distinct covered task identifiers, including outgoing and proposed tasks in the checked coverage |
| `participants`, `unavailable` | Workers whose saved input was considered; unavailable worker-to-reason map, accounting for every checked worker with no overlap |
| `sources` | Receipts for inspected evidence files, each with canonical absolute `path`, SHA-256 `sha256`, and byte `size` |
| `note` | Receipt for the installed immutable Markdown bytes |
| `coverage` | Versioned worker-specific receipts binding outgoing work and the proposed transition |
| `input_digest` | Canonical metadata digest used to detect changed retries |

The saved note begins with a JSON metadata object between `---` delimiters:
`schema_version`, `id`, `completed_at`, `period_start`, `period_end`, `triggers`,
`tasks`, `participants`, `unavailable`, `sources`, and `coverage`. The foreman's
Markdown follows it. Recording retries retain the original completion time.

Coverage binds the worker's latest original assignment identity, available
dispatch identity, live native/session/process evidence, report digest or stated
unavailability, and proposed role/model/effort/context and input paths. A recorded
transition links that coverage to the utility's completed boundary and replacement
identity. Later changes to another worker's assignment do not invalidate it.
The owner rechecks relevant source bytes and live identity before applying coverage;
an old note or Herdr completion label alone proves no present transition authority.
The source's nullable `dispatch_evidence` contains the original dispatch row's
`sha256` digest and nullable `report` receipt for its recorded implementation
review. That review is distinct from the worker's own report. A changed dispatch
record or review file invalidates its worker's coverage.
An unreadable known review may have a null current `report` only with the source's
explicit `unavailable` reason; its archived dispatch metadata remains bound by
`sha256`. Restored readable bytes invalidate that recorded missing condition.
After home migration, completed coverage and transition bridges accept a saved
receipt path through the retained legacy-home link only when it resolves to the
current path with the same byte digest and size. The historical spelling remains
unchanged; another location or changed bytes invalidates the authority.

Each `transitions` entry has `schema_version: 1`, unique content-derived `id`,
UTC `at`, `agent`, the original `descriptor` coverage, and the verified `incoming`
observation. The descriptor must match saved retrospective coverage or prove an
exempt first start; a receipt through the retained legacy-home link is the same
saved coverage under the migration rule above. The transition receipt bridges
only the utility's own recorded boundary to that incoming worker; it does not
cover later outgoing work.

The cadence uses the latest completed retrospective, or the established first-work
baseline when no retrospective exists. Failed checks and incomplete notes never
advance it. Existing work with no usable history is due immediately. Coverage for
a proposed transition is independent of the daily due decision.

- **Writer** — the utility validates recording metadata and required nonempty
  synthesis sections, reads source bytes, and atomically installs the completed
  note before committing the index. The foreman judges the content's substance.
  Writes serialize under the sidecar lock. Identical retries preserve the existing
  record and its completion time; conflicting IDs or pending transactions fail
  with a diagnostic. A journal preserves interrupted recording for reconciliation.
  Home migration retains receipt path spellings, rebases the journal's ancestry
  digest to the rewritten index identity, and resumes idempotently after a
  failure between those two atomic writes.
- **Readers** — `retro-list` and `retro-show` accept schema 1 and verify installed
  note digests. They read without Herdr, config, dispatch-state migration, or
  writes. `retro-list --since` includes records completed at or after its cutoff;
  `retro-show` selects the latest completion unless an ID is supplied. Historical
  evidence sources may be unavailable after cleanup; the saved note remains
  retrievable. Applying its transition coverage still revalidates source evidence.
- **Missing, corrupt, or unsupported** — a missing history means no prior
  retrospective. It never proves an existing worker is new or authorizes a send.
  Preserve orphan notes, pending transactions, corrupted files, and newer formats;
  report the diagnostic and restore original bytes or update the owner. Never
  overwrite them with an empty index or manufacture completion coverage.
- **Migration** — version 1 is the first format. Only `herdr-foreman` may migrate
  documented older formats, preserving notes and historical records. Future shape
  changes bump the document and affected record versions. An unsupported reader
  has no usable prior state and must not launch, clear, retry, or reset counters
  from that fallback.

Execution, source collection, note structure, and command inputs:

```text
skills/herdr-foreman/references/retrospectives.md
```

## State Record Format

```json
{
  "schema_version": 9,
  "snapshots": ["<measure output>, oldest first, ring capped at 20"],
  "assignments": [
    {
      "schema_version": 9,
      "at": "2026-09-01T21:00:00+00:00",
      "role": "developer",
      "agent": "grok",
      "status": "applied",
      "cleared": false,
      "clear_reason": "retained",
      "task": "owner/repo#322",
      "fix_round": 1,
      "context_session": {"pane_id": "w4:p1", "source": "herdr:grok", "agent": "grok", "kind": "id", "value": "native-session-id"},
      "tier": null,
      "requirements": null,
      "reviewer_scope": null,
      "judge_mode": null
    }
  ],
  "specialist_assessments": [],
  "recovery": {
    "schema_version": 13,
    "tasks": {},
    "checkpoints": [],
    "plans": [],
    "dispatches": [],
    "context_permissions": [],
    "events": [],
    "refusal_authorizations": [],
    "diagnoses": [],
    "legacy_ruling_recoveries": [],
    "approaches": [],
    "hand_clearances": [],
    "historical_attempts": [],
    "role_clearances": [],
    "delivery_recoveries": []
  }
}
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `9`. Version 9 adds `judge_mode` to every row: the judge seat's declared mode, `unknown` for a judge row migrated from before it, null for other roles. Version 8 drops the retired qualification battery's summary from `tier`; migration removes it from older rows. Version 7 adds `pressure_headroom` and `de_escalated` to a row's `tier`; an older tier row migrates to null headroom and `de_escalated: false`, since nothing could de-escalate before it. Bumped on any shape change |
| `snapshots` | array | Whole `measure` documents, oldest first; the ring holds the last 20 |
| `assignments` | array | Append-only ledger of who held which role |
| `snapshots[].schema_version` | integer | Currently `3`. Version 2 added `window_group`; version 3 adds per-round `tier_billing`. Older snapshots migrate on read, preserving headroom and shared-window membership |
| `snapshots[].agents[].window_group` | string | The usage window this agent shares with others; empty means a window of its own. Present on every agent record, including skipped and failed ones — pool membership is config, not a measurement result |
| `assignments[].schema_version` | integer | The row's own version, stamped on write |
| `assignments[].at` | string | ISO-8601 timestamp, from `--now` or the CLI's clock |
| `assignments[].role` | string | The role handed out |
| `assignments[].agent` | string | The agent that received it |
| `assignments[].status` | string | `applied`, `sent_but_not_started`, or `unknown` |
| `assignments[].cleared` | boolean or null | Whether the dispatcher confirmed its automatic clear; null means historical evidence is unavailable |
| `assignments[].clear_reason` | string | `automatic` with cleared true, `hand` or `retained` with cleared false, or `unknown` with cleared null |
| `assignments[].task` | string or null | Non-empty stable task identifier; null for older or unlabelled assignments |
| `assignments[].fix_round` | positive integer or null | Task's fix number; null for initial development or non-fix work |
| `assignments[].context_session` | object or null | Verified native session reference scoped to a pane: `pane_id`, `source`, `agent`, `kind`, `value`, all non-empty strings; kind is `id` or `path`. Null means continuity was not established |

| `snapshots[].agents[].tier_billing` | object | Round → `{model, effort, window}` for configured tiers; unmeasured attribution is `unknown`. Empty for older snapshots |
| `assignments[].tier` | object or null | Requested `round`, selected config `tier_row`, `kind`, `model`, `effort`, declared/effective multipliers, billing window, launch options, input `prompt_hash`, `pressure_headroom` and `de_escalated` (the measured headroom the selection used, and whether the tier the worker runs at still falls short of a declined discretionary escalation), and `verified` proof. Null for old or non-tiered dispatches |
| `assignments[].requirements` | object or null | Normalized requirement object from the assigned role in the plan; null for legacy assignments |
| `assignments[].reviewer_scope` | string or null | Reviewer participation recorded as `verification`, `design`, or `unknown`; null for other roles. Older reviewers migrate to `unknown` |
| `assignments[].judge_mode` | string or null | The mode the judge seat was dispatched for: `adjudication`, `diagnosis`, or `unknown`; null for other roles. A weighing is an `adjudication`; its report is the ruling file `skills/release/dismiss-ruled-review.sh` reads, never a record this state owns. Older judge rows migrate to `unknown`, and a reconciled dispatch whose receipt predates the field records `unknown`. A live judge dispatch with no declared mode is refused, never defaulted |
| `specialist_assessments` | array | Append-only report-contract assessments with original dispatch and byte receipts; each record has its own schema version |

`verified` contains `model`, `effort`, `argv`, `source` (`launch_argv` or
`process_argv`), and `pane_id`; process proof also contains `pid`. Loading
state rechecks that the argument vector proves the recorded pair. Stored
proof never replaces checking the live process before retained dispatch.
`prompt_hash` hashes the original assignment message, common file, and brief
as length-framed byte strings; the generated metadata footer is excluded.

Every hand-off is recorded, one that never started included: the ledger is what
the tool did, and a round that went out and died is the thing worth looking up
afterwards. `status` keeps that honesty out of the plan — `role_counts` skips
rows marked `sent_but_not_started`, so an assignment nobody began never counts
as experience of the role. The skip list is a deny-list: a version 1 row
migrated forward carries `unknown` and still counts, which says the tool cannot
prove the outcome rather than that the history should vanish.

Every record carries `schema_version`, not only the document: a ledger row
outlives the document it arrived in, and a version on the row is what makes a
later migration auditable row by row. Each snapshot is a whole `measure`
document and arrives already stamped.

## Recovery records

The recovery document uses `schema_version: 14`; individual records retain their
independent versions. Version 6 adds the dispatch fields `brief_identity`, `refusal` and
`refusal_move` and the `refusal_authorizations` collection; version 7 adds the
dispatch's send-time `provider`; version 8 adds the `diagnoses` collection;
version 9 adds `legacy_ruling_recoveries`; version 10 widens `dispatches[].role`
to a seat, per Seat vs responsibility above; version 11 adds the `approaches`
collection; version 12 adds `judge_mode` to a judge dispatch, its
`context_before_send` and its saved result, and binds it into the dispatch
fingerprint, so an adjudication and a diagnosis of one brief are separate
dispatches. Only judge dispatches carry the field. Version 13 adds the
`task_closed` event kind. Version 14 adds `oracle` to a mechanical round's
dispatch and binds a `patch` or `fixture` oracle's pin into the dispatch
fingerprint (#585). The
owner stamps an older store on load, adds the empty collections, and refuses one
already carrying a field — or a seat-named dispatch — its version did not own. Generic records remain version 1; stale-Grok delivery and
composition-bearing dispatch/result records use version 2; a judge
dispatch/result carrying its `judge_mode` uses version 3. Version 1 and 2 rows
are never restamped: a judge dispatch recorded before version 3 keeps no mode,
and its ledger row reads `unknown`. A new judge reservation without a mode is
refused, and so is a mode-less retry of a stored row; only stored rows keep
the mode-less shape.
`context_before_send`, wherever present, is an object. On a mode-bearing dispatch it carries
`judge_mode` equal to the dispatch's own; on any other dispatch it carries no
`judge_mode` key, null included. This holds from the send onward, pending or
applied, and a version-3 dispatch past `reserved` without the object is refused. Checkpoints are at
version 2: the owner upgrades a version-1 row on load, stamping it and
preserving its identity, fix round, base and recorded ruling, and refuses one
missing the ruling evidence its version required. The owner adds empty `role_clearances` and
`delivery_recoveries` arrays when migrating versions 1 or 2. Version 1 also
gains empty `hand_clearances` and `historical_attempts` arrays. Existing
record shapes, contents and evidence remain unchanged. Recovery 4 → 5 changes
only the enclosing version. Older stores containing future dispatch fields or
versions are refused without rewriting. State and assignment versions migrate
independently; snapshot schema 3 remains unchanged.
Every record carries `at` and `task`. Authorizations contain the actual operator
message `source` and `quote`; evidence receipts contain absolute `path` and
`sha256` of the bytes read by the owner. Receipts are audit evidence, not a
replacement for live readiness, source review, or release gates.

| Collection | Record fields and relationships |
| --- | --- |
| `tasks` | Keyed by original task identity; `task`, immutable full `base_revision`, `scope`, `allowed_paths`, `authorization`. Migration invents none of them. |
| `checkpoints` | Record schema 3. Unique `id`, `fix_round`, original `base_revision`, concrete `defect`, `previous_attempts`, `progress`, `change_in_approach`. Carries `judge_agent`, `judge_report` and `judge_evidence` only when a ruling is cited, and a cited one requires a completed pinned-judge assignment after the preceding developer attempt plus the `requested_by` receipt (source and quote) for the operator request it answers. A partial trio is refused, one task records at most one cited ruling, and `requested_by` without a cited ruling is refused. Version-1 rows migrate to 2, the shape that predates the receipt; a version-2 row carrying one is refused, and neither older version has a receipt invented for it. The reader accepts versions 2 and 3. |
| `plans` | Unique `id`, `checkpoint`, original `base_revision`, `scope`, `allowed_paths`, `additional_fixes`, derived `first_fix`/`last_fix`, `authorization`; optional `supersedes` references a preserved prior approval. |
| `dispatches` | Unique `id`, byte/input `fingerprint`, `role` (the SEAT, per the Writer / Reader Contract's Seat vs responsibility), `agent`, cumulative `fix_round`, `plan` or null, `work` or null, `status`, `result`, `report`, and `assignment_index` once an outcome is recorded. CLI records `brief`, `common`, `observed_before`, and `context_before_send`; reconciled retries preserve `prior_assignment_indices`. `provider` is the worker's config `kind` at send time, authoritative for the refusal's attribution. `brief_identity` digests the common and role brief bytes with the enrolled report path masked. From #460, `brief` and `common` name frozen copies under the source's `.dispatched/` directory (from #554, the source directory's canonical path; a recorded path with a symlinked component is refused when read back), named by their content's sha256 and never rewritten (a link or non-regular file there is refused, and so is a `.dispatched/` that is itself a link), so the prompt a recovery rebuilds reads the bytes that were checked and sent. A dispatch whose complete identity under its source paths is already recorded keeps those paths, so its replay still matches: the same `fingerprint` (or a judge row's pre-mode form) on an `applied` row, whose replay returns its saved receipt and sends nothing. A retry of a row never sent, and a later correction over the same files, are new sends and are frozen; a source brief that changes during the replay is refused; a batch mixing replays with new roles is refused (`assign.freeze_decision`). `refusal` (`provider`, `reason`, `receipt`, `report_path`, `evidence`) appears once `record-refusal` binds an exit-5 receipt to an applied row's enrolled report; `refusal_move` (`from`, `from_provider`, `provider`) appears on the dispatch that carried the refused brief, same `brief_identity`, to another provider. `oracle` (from recovery store 14) appears on a dispatch whose plan round is `mechanical` with an oracle: the oracle as the plan licensed it, a `digest` with its `value` or a `patch` or `fixture` with its `path` and the `sha256` its plan pinned. `apply` refuses a pinned file that no longer hashes to its pin before anything is sent, and folds the pin into the `fingerprint`; `verify-oracle` checks the round against this field and refuses a plan that no longer declares it. |
| `context_permissions` | Original `assignment_index`, `next_fix`, `reason`, `authorization`, `evidence`, `evidence_receipt`, later `observed_session`, and `basis: operator_authorized_fresh_handoff`. The original null session is never replaced. |
| `events` | Append-only `sequence`, `kind`, and structured `details` preserving approvals, waiting states, reservations, send transitions, results, transport retries, superseded review receipts, and recovery decisions. Kind `task_closed` carries `details.outcome` (`merged` or `abandoned`) and `details.evidence`; it ends the task's developer reservation until a later developer assignment reopens the task. Owned from recovery store version 13; an older store carrying one is refused as newer data. |
| `hand_clearances` | Unique `id`, original release `assignment_index`, `previous_developer`, complete owner `input`, clear byte `receipts`, later `observed_session` or null, and `basis: verified_required_release_clear`. Both indices retain their original rows. The later observation never substitutes for historical proof; changed or missing current IDs do not invalidate archived clear evidence. |
| `role_clearances` | Unique `id`, original `task`/`base_revision`, developer `assignment_index`, actual `clearing_assignment_index` and `clearing_dispatch`, `next_fix`, complete owner `input`, clear/authorization byte `receipts`, reused or explicit `clear_authority`, later `observed_session`, `basis: verified_authorized_role_clear`, and `grants_future_attempts: false`. The input fixes the same work and correction plan used for dispatch. Original known native proof and every earlier row remain unchanged. |
| `historical_attempts` | Unique `id`, actual `fix_round`, `previous_developer`, appended `assignment_index`, original owner `input`, authorization/transport/report byte `receipts`, inspected `vcs` checkout/head/diff evidence, `basis: completed_authorized_manual_correction`, null `native_session_proof`, `grants_future_attempts: false`, and append-only `reviews`. |
| `diagnoses` | Record schema 3. Unique `id`, `task`, `checkpoint`, `fix_round`, original `base_revision`, `remedy` (`continue`, `restructure` or `stop`), `bound` or null, `reissue` (whether this diagnosis repeats its predecessor's rung), `approach` naming the approach it was recorded under or null for the initial one, `approach_change` naming the approach it approved or null, `judge_agent`, `judge_evidence`, `investigator_report` binding the assessed report the judge ruled on or null on a migrated row, `scope`, `allowed_paths`, the `plan` a bounded remedy authorized or null, `supersedes` naming a replaced plan or null, and the operator `authorization` that permitted an early supersession or null. The remedy ladder is read per approach: within one approach the diagnoses move down it, each nonterminal rung repeating at most once, and `stop` is terminal there and never repeats. A diagnosis approving a new direction records its `bound` as that approach's allowance and no `plan`. Version-1 rows migrate with `reissue: false` and `investigator_report: null`; version-1 and version-2 rows migrate to 3 with `approach: null` and `approach_change: null`. |
| `approaches` | Record schema 1. Unique `id`, `task`, `checkpoint`, original `base_revision`, `from_fix` (the cumulative attempts spent before this direction started), `allowance`, `direction`, `verification`, `origin` (`diagnosis` or `operator`), `diagnosis`, `judge_evidence` and `investigator_report` on a diagnosed reset, `authorization` on an operator override, and `supersedes` naming a retired prior-approach plan or null. `from_fix` equals its checkpoint's `fix_round` and increases across a task's approaches; a direction is recorded once per task. A superseded plan belongs to the same task and base, and no longer authorizes attempts. The task's cumulative attempt numbering is never renumbered. |
| `refusal_authorizations` | Unique `id`, `task`, `role`, `fix_round` or null, approved `provider`, `brief` (`unchanged` or `revised`), the operator's `decision`, `authorization`. Permits one dispatch on its key to that provider after two recorded refusals, the brief held to the refused identity unless `revised`; the consuming dispatch names it in `refusal_move.authorization`. |
| `delivery_recoveries` | Unique `id`, original `dispatch` and `assignment_index`, owner `input`, byte `receipts` for report/negative wait/pane/visible/native source/common/brief, archived `native_session`, `found: true`, `basis: archived_native_final_source`, null `native_session_proof`, and `grants_review_approval: false`. Original null session evidence is preserved; the archived user prompt binds its delivery to the saved dispatch. |
| `legacy_ruling_recoveries` | Schema-1 receipts already written for historical version-2 citations. Unique `id`, `task`, `at`, operator `authorization` source/quote, `backup` path/SHA-256, `checkpoints` mapping original IDs to canonical JSON SHA-256 digests, and `grants_future_attempts: false`. Reading validates these bindings without fetching historical files. Altered, new or overlapping citations do not inherit a receipt. Empty after a schema-8 migration. |

Dispatch/result version 2 carries `requirements`, `reviewer_scope`, or both.
Requirements contain the assigned role's normalized object; reviewer scope is
`verification` or `design` and appears only on a reviewer dispatch. Absent fields
are omitted, not null. The result preserves the dispatch's exact metadata and
matches its assignment row. Version-1 dispatches/results retain their original
shape and cannot carry these fields. Unknown-send reconciliation preserves the
metadata without inventing native continuity. Optional requirements and retained
specialist intent enter the dispatch fingerprint only when present; legacy retry
identities remain unchanged.

### Specialist assessment records

`assess-specialist` appends records to the main state's `specialist_assessments`.
Its input is exactly `id`, `dispatch`, `report` and `delivery`; `outcome`,
`summary` and `contribution` are refused by name. The owner is
`skills/herdr-foreman/foreman/engagement.py`; the line parser is
`skills/herdr-foreman/foreman/report_contract.py`, whose module docstring holds
the line formats and refusal classes.

Each schema-2 record contains `schema_version: 2`, `id`, `at`, `dispatch`,
`assignment_index`, `task`, `role`, `agent`, `report`, `delivery`,
`report_evidence`, `delivery_evidence`, `source`, `brief_evidence`, `criteria`,
`acceptance`, `verdict`, `contribution`, `legacy` and `gap`. The evidence objects
contain absolute `path` and SHA-256 `sha256`. The report is the supervised
assignment's enrolled path; delivery is saved successful `wait-report` JSON for
that worker and path, or the exact owner-recorded `recover-report` output for
that dispatch and the same report bytes.

| Field | Meaning |
| --- | --- |
| `source` | `report` for a record parsed from the report's lines; `contribution_only` for the declared `design` or `implementation` contribution of a report refused for a contract gap; `foreman_assessment` for a migrated schema-1 record |
| `brief_evidence` | Receipt of the frozen brief `apply` sent; a consultation's only, else null |
| `criteria` | `N`, re-derived from that brief's `CRITERION` block; a consultation's only, else null |
| `acceptance` | `[{k, state, evidence}]` for `k` in `1..N`, `state` `met` or `unmet`; a consultation's only, else null |
| `verdict` | `blocking` or `approved` where the role or dispatch specialty requires the line, else null |
| `contribution` | The report's optional `CONTRIBUTION:` value or null; a migrated record keeps its old value |
| `legacy` | `{outcome, summary}` on a migrated record, else null |
| `gap` | `{message, gaps}` on a `contribution_only` record: the refusal it replays on an identical retry (same input, same report bytes); else null |

The role and specialty come from the owner dispatch, never from the caller. A
report with a contract gap appends nothing, save a `contribution_only` entry
when it declares `design` or `implementation`; that entry has null line fields
and satisfies no reader below. The utility verifies the original
confirmed dispatch, assignment, enrollment and frozen brief before appending.
Exact ID/input retries preserve the original receipt, including after source
cleanup; changed input requires a new ID. Historical reads validate schema and
relationships without reopening sources.

A report-sourced record gates three readers. `close-member` needs one matching
the current report bytes before an `accepted` reviewer, tester or consultation
closure, and for a consultation every criterion `met`. `diagnose` and the
judge-investigation gate count only investigator records with every criterion
`met` and no `blocking` verdict. Acceptance itself is contract completeness,
whatever the verdict; a blocking verdict gates the round, never the assignment. A warm follow-up needs one for the preceding assignment and revalidates
its report and delivery bytes and prior supervision disposition.

A `design` or `implementation` contribution on any record adds a same-task
independence exclusion; nothing subtracts one. Missing, corrupt or unsupported
assessment history follows the main state's preserve-and-refuse writer contract.

Migration `1 → 2` runs in the owner while state loads, and is idempotent. It
moves `outcome` and `summary` under `legacy`, sets `source:
foreman_assessment`, nulls the line fields and keeps `contribution`. A migrated
record never satisfies `accepted`, a warm follow-up or the diagnose and judge
gates; an in-flight consultation needs a fresh dispatch. A schema-1 record
already carrying a schema-2 field is refused as corrupt. A record newer than 2
takes the lagging-reader path: no usable prior state, the file left untouched.
The document's own version is unchanged.

Version 4 admits delivery record schema 2 alongside unchanged schema-1 receipts.
The new record uses `basis: archived_grok_clear_source`, preserves `native_session`
as Herdr's archived observation, and adds `source_session` with `agent: grok`,
`kind: id`, and the native updates' `value`. Its `input` and `receipts` also bind
the original `plan`. All continuity and approval fields retain their prior meaning.
Migration from version 3 changes only the enclosing version, never an old record.
The source identity is delivery evidence only, never a Herdr observation or
retained-context proof. Version-3 readers refuse version 4 without migration.

Dispatch statuses are `reserved`, `sending`, `sent_but_not_started`, `applied`,
and `not_sent`. The first three hold an unresolved slot. Only confirmed
developer assignments advance the task's fix count; a pending slot blocks a
second implementation/release dispatch for that task or worker. `applied`
results must match their referenced assignment. Extra fixes require a matching
plan and work bounds, including when a reader validates historical state.
A completed legacy manual correction instead requires its linked historical
import and original bounded authorization; that record grants no future fixes.
Its appended assignment uses the original completion time, `role: developer`,
`status: applied`, actual task/agent/count, `cleared: null`,
`clear_reason: unknown`, null session and tier. It is historical work recorded
now, not a contemporaneous owner dispatch. All assignment count readers consume
that same row; they never add the import record a second time.
Each historical review is a version-1 record with `at`, `task`, unique `id`,
original `input` (attempt/head/verdict/mode/reviewer/report), and byte `evidence`.
It grants no attempt; an existing remaining plan requires its latest blocking
receipt before another correction. A full approval cannot stand in for a
blocking finding, and scoped review cannot approve a release.

`work` contains `base_revision`, `scope`, repository-relative `paths`, and
blocking `findings`. A review receipt contains `dispatch`, `head_revision`,
`verdict`, `review_mode`, independent `reviewer`, `report`, `changed_paths`, and
`evidence`. The foreman verifies the actual VCS diff before recording these fields;
the command reads the report, checks its stated head, and records its digest.
The next approved correction rechecks the preceding blocking report's bytes.
An approval receipt requires full review; tester and external gates remain
separate requirements in the skill.

`context_before_send` records clear handling, native session observation,
tier proof, and any fresh `transition`. A confirmed result's
`context_transition` names `release_handoff` with prior developer/release
indices, or `authorized_context_recovery` with the original assignment and
permission reference. `verified_hand_release_handoff` names the original
developer/release indices and `clearance` identity. `historical_correction_handoff`
names the imported developer index, `historical_attempt` identity and
`continuity: unproven`. `verified_role_clear_handoff` names the original
developer, `clearing_assignment` index and `clearance` identity. None changes
native-session proof or grants an attempt.
`reconciliation` records its own version/timestamp,
original `input`, `evidence_receipt`, and later `observed_state`/`observed_session`.
An applied recovery appends a new assignment with null contemporaneous session
proof and marks its result `recovered: true`; it preserves the original row.

Each snapshot is one `measure` document: `schema_version`, `measured_at`, an
`agents` object keyed by agent name (`kind`, `state`, `herdr_state`,
`state_source`, `pane_id`, `windows`, `credits`, `plan`, `headroom_pct`,
`skipped`), and `failed_agents`. A failed agent carries `error.code`,
`error.message`, and `error.details`; a usage failure whose pane shows the
pending-update banner sets `error.details.pending_cli_update` to true so the
round preflight can name its maintenance relaunch command. `headroom_pct` is the minimum `remaining_pct`
across that agent's windows. `state_source` is `herdr` or `probe`, naming which
signal decided `state`; `herdr_state` carries what herdr claimed. `plan` is an
informational plan name and never feeds headroom.

## Writer / Reader Contract

- **Writer** — `measure` appends a snapshot; labelled `apply` reserves before
  clear/relaunch, persists sending before terminal input, and appends the
  confirmed or unconfirmed outcome before cosmetic labels. Owner commands
  manage task/approval/recovery records; all mutations preserve audit events.
  Writes are atomic: temp file in the same directory, `fsync`,
  `os.replace`.
- **Readers** — `plan` reads the newest snapshot plus the ledger (role history
  breaks a headroom tie), and the config's `role_costs` for its seat weights;
  `state` prints the document. Neither appends records; their shared loader performs owner migrations. Live `apply` reads the most
  recent assignment for the named worker before retaining context; Step 10
  documents the retained-dispatch contract. `status` derives budgets and paused
  implementation separately from active audit work. `apply --dry-run` reads
  current recovery bounds without writes; an older ledger requires an owner
  `state` command first. Dry-run never proves live continuity.
  `cost-report [--task TASK]` reads the ledger, the recovery events and the
  snapshots without writing or migrating, and refuses an unusable state file
  rather than reporting no tasks. Its output is
  `{"schema_version": 1, "unrecorded": [...], "tasks": [...]}`: per task its
  `status` (`accepted`, `abandoned` or `open`), `started_at`, `ended_at`,
  `elapsed_seconds`, `tokens`, `correction_rounds`, `work`, `coordination` and
  `windows`, each reported separately. `unrecorded` names every field no owner
  record carries. A window's movement carries `attribution: unknown` unless the
  report's attribution predicate holds. What each field counts, and that
  predicate, are the contract of `skills/herdr-foreman/foreman/cost_report.py`
  (module docstring), not restated here.
  `skills/herdr-foreman/prune-report-caches.py` reads `recovery.dispatches[]`
  `brief` and `common` paths to find reports directories, without writing or
  migrating; an unusable or unmigrated state file is its `could_not_check`,
  never an empty ledger. Its contract is that script's top-of-file docstring.
- **Seat vs responsibility** — a partitioned round plans several seats of one
  role (`reviewer#api`, `reviewer#core`). `assignments[].role` holds the
  RESPONSIBILITY (`reviewer`), so per-role history, independence and rotation
  read one role instead of fragmenting across slice names.
  `recovery.dispatches[].role` holds the SEAT, which is what a slice's verdict
  is read back through. A reader comparing the two resolves the dispatch's
  responsibility first (`tiers.canonical_role`); the two strings are equal only
  on an unpartitioned round. Which roles are seatable, and the grammar a seat's
  slice half follows, are in `skills/herdr-foreman/references/review-partition.md`.
- **Assignment chronology** — assignment `at` records the event time; import receipt
  `at` records when the owner appended its evidence. Reads preserve original row
  indices and never reorder the audit. Chronological lookup returns the original
  row/index or refuses unknown ordering; its contract is in
  `skills/herdr-foreman/foreman/chronology.py` (`latest_assignment`).
- **Fix history** — live developer fixes advance the task's confirmed fix
  number even when the worker changes. An initial assignment cannot reset a
  task that already has a confirmed developer assignment. `apply` uses the ledger's task and
  outcome evidence, never pane labels, for that check.
- **Session continuity** — a fresh labelled developer dispatch reads Herdr's
  native reference after clearing and correlates it after confirmed first-prompt
  delivery, including delayed IDs. A Grok `/new` without an observed pre-clear ID
  keeps null continuity; a later Herdr ID alone cannot establish its freshness.
  The recovery reference names the delivery continuation.
  Unproven correlation is null without losing
  the confirmed dispatch. An unchanged pre-clear reference cannot prove a new conversation. A
  retained dispatch checks the recorded identity against the live source at
  readiness and immediately before sending. Missing, changed, malformed, or
  non-native identity is a refusal with no terminal writes. Other assignments
  and unlabelled development record null. Requirement-bearing consultations also
  preserve verified native context for the specialist follow-up contract. The official integration must report
  native session changes; check its installation when continuity is unavailable.
- **Serialization** — CLI owner transactions use a live OS lock at the state
  path plus `.lock`, including readers that may migrate. Contention refuses
  before dispatch. The file's presence alone means nothing; an OS process
  holding its lock establishes ownership. Dry-run and worker launch write no
  ledger or lock file.
- **Absent state** — a first run has no file. Every reader treats that as no
  prior state and continues; `plan` still requires a snapshot, passed with
  `--snapshot` when the state file holds none.

## Continuity Stores

The independent continuity stores do not change this dispatch-state schema.
`herdr-foreman` owns their shape and is their sole writer:

| Store | Canonical location | Contract |
| --- | --- | --- |
| Working lessons and foreman handoffs | `<selected-state>.memory/index.json` | `skills/herdr-foreman/references/working-memory.md`, Persistence contract |
| User attention and recorded progress | `<selected-state>.attention.json` | `skills/herdr-foreman/references/attention.md`, Commands and files |
| Fleet observations and supervision | `<selected-state>.supervision.json` | `skills/herdr-foreman/references/supervision.md` |
| Model capabilities, sourced and dated | `<selected-state>.capabilities.json` | `skills/herdr-foreman/references/model-tiers.md`, Capability table |
| Report gates from classifier labels and blocking verdicts | `<selected-state>.report-gates.json` | `skills/herdr-foreman/references/report-classifier.md`, Sidecar schema 2 |

Resolve the selected state path before deriving these locations. Each store and
its records have their own schema version and lock. Their offline readers never
migrate or write dispatch state. Preserve these files during task cleanup and
include their locations in foreman handoffs. A saved observation is never task
acceptance or permission to act. The referenced contracts own full field shapes,
retry and unsupported-schema behavior; only their owner commands mutate them.

### Supervision schema 1

`skills/herdr-foreman/foreman/supervision.py` owns `<canonical selected state>.supervision.json`.
The document has `schema_version: 1`, canonical `state_path`, nullable `binding`,
and arrays `members`, `events`, `acknowledgements`, `holds`, and `watchers`.
Each array entry, binding, refinement, resolution, disposition, and evidence
receipt carries `schema_version: 1`. Timestamps are timezone-aware ISO strings.
Readers reject unsupported versions or corrupt records without migrating or
replacing them. A missing never-bound store is empty; loss of a bound owner's
store requires recovery of its history before rebinding or writing.

- `binding`: `{schema_version, at, generation, identity, state_path}`.
  `generation` is a positive, increasing integer for native foreman changes.
  `identity` contains `kind: id|path`, native `value`, canonical `cwd`,
  `herdr_env`, and `pane_id`. Path identities use an absolute native transcript
  path. The same binding is also saved at the discovery path documented in the
  supervision reference. First use saves an empty unbound owner before discovery;
  mutations require the binding to commit. Discovery is written before owner
  binding; an ahead
  generation blocks an incomplete handoff. An older native session stops being
  the foreman once the owner's newer generation commits.
- `members`: `{schema_version, id, at, assignment, active, observed, resolution,
  refinements}`. `assignment` contains `{id, agent, task, report, pane_id,
  native_session}`; `id` equals the stable dispatch ID, `report` is absolute,
  and unknown pane/native identity is null. `observed` is the latest map of
  opaque observation keys to JSON values; it grants no acceptance.
  `refinements` append `{schema_version, at, pane_id, native_session}` and fill
  missing expectations only. `resolution` is null while `active: true`;
  otherwise it is `{schema_version, at, outcome, evidence}`. Only the foreman's
  explicit resolve command retires an enrollment.
- `events`: `{schema_version, id, seq, at, member, kind, data}`. Sequences are
  contiguous positive integers and IDs are `event-<seq>`. `member` is an
  enrollment ID or null for fleet events. `kind` names the observation or
  scheduled recheck; `data` preserves its JSON payload. Events append only.
- `acknowledgements`: `{schema_version, at, event, outcome, evidence, pending,
  recheck_at, input_digest}`. Each event has at most one acknowledgement.
  `pending: false` requires null `recheck_at`; pending outcomes have an explicit
  or script-default future recheck timestamp. `input_digest` binds the original
  per-event command input, excluding snapshot `through`, so an exact retry
  preserves the receipt and schedule
  after evidence changes or the deadline passes. Snapshot `through` bounds
  which event IDs a command can acknowledge; later events remain pending.
- `holds`: `{schema_version, id, at, kind, resume_condition, evidence,
  dispositions, resumed_at, through, members}`. `kind` is `waiting_for_user` or
  `handoff`; `resumed_at` is null until explicitly resumed. `through` captures
  the handled event boundary and `members` hashes the active assignments.
  `dispositions` contains `{schema_version, member, outcome, evidence}` for
  every active enrollment. New events or assignments invalidate that coverage.
- `watchers`: `{schema_version, id, at, heartbeat, deadline, process, status,
  reason, ended_at}`. `process` contains positive `pid` and an `identity` digest
  of its observed start time and argv. `status` is `running|stopped`; `reason`
  and `ended_at` are null until the watch stops. Health also checks a fresh
  heartbeat and bounded deadline. A PID alone proves no live watcher.
- `evidence` arrays contain readable file receipts:
  `{schema_version: 1, path, sha256, size}` with canonical absolute `path`,
  lowercase 64-character SHA-256, and nonnegative byte `size`. They preserve
  observed bytes, never infer that a prose claim is true or an action authorized.

The native Stop reader performs local read-only checks for the exact bound foreman.
It never migrates state, acknowledges events, clears attention, or marks task
completion. Its normal no-binding result applies only to a session never bound
as foreman; missing or unreadable bound-owner history cannot release obligations.

## Migration

Only the owner migrates, and it reads a version in one of three directions.

- **Older** — the document, or a single ledger row, is walked up the
  `MIGRATIONS` / `RECORD_MIGRATIONS` chain in
  `skills/herdr-foreman/foreman/state.py`, keyed by the version being upgraded
  from, and the upgraded file is rewritten in place. A document or row carrying
  no `schema_version` reads as the pre-versioning version `0`, which is what
  gives the chain a step below `1`. The `1 → 2` step stamps `status: unknown`
  on every row written before the field existed. The `2 → 3` step preserves
  status and role history while adding `cleared: null`, `clear_reason: unknown`,
  `task: null`, `fix_round: null`, and `context_session: null`. It cannot invent evidence of a retained
  session. The `3 → 4` step adds `tier: null` and preserves all task, fix,
  status, and native-session evidence. The `4 → 5` step adds an empty recovery
  document and stamps preserved assignment rows; it never infers the original
  task base, authorization, or missing native identity. State `5 → 6` adds empty
  `specialist_assessments`; assignment `5 → 6` adds null `requirements` and
  `reviewer_scope: unknown` for reviewers, null for other roles. Migration never
  assumes an older reviewer only verified work. Unexpected newer fields in an
  older document or row refuse migration. Snapshot `2 → 3` independently adds
  empty `tier_billing` maps, preserving window groups and readings. Each row is migrated even in
  a document already at the current version.
- **Newer** — this build is the lagging reader, not the migrator. The caller
  gets an empty document in memory, the file on disk is left exactly as found,
  and a warning goes to stderr. A single row stamped ahead of this build makes
  the whole document unusable rather than being dropped, so the next write
  cannot lose it.
- **Corrupt** — unparseable JSON, a non-object document, a non-array field, a
  non-object row, invalid context or tier evidence, inconsistent recovery
  relationships, a duplicated or out-of-order approach, or an extra fix without its recorded allowance: no usable
  prior state, treated like the newer case.
  The file is never deleted, and the warning never instructs the operator to
  discard it. Losing a snapshot ring costs one re-measure; overwriting an
  unread file costs the ledger.

A tool failure is not a version case: unreadable permissions or a directory in
the state path still raises, carrying the command that fixes it.

Bump `schema_version` for any shape change; never repurpose a field. The skill
and the utility ship in the same plugin version, so writer and readers move
together — `rules/stateful-artifacts.md` Cross-Pipeline Schema Bumps does not
apply here.

## Hints, Not Authority

- A `headroom_pct` that is not a finite number — a string, an object, `true`,
  `NaN` — reads as unknown with a warning naming the agent and the value,
  never a crash. Unknown already has a defined place in the ordering, and a
  numeric string is coerced rather than discarded.
- A snapshot is a last-seen reading, never ground truth. `plan` may run off a
  stale snapshot deliberately; planning has no side effects.
- `apply` never trusts a snapshot for an agent's lifecycle state. It re-reads
  the live agent through `herdr agent get` and refuses a `working` or `blocked`
  worker before sending a single keystroke.


## Explicit legacy ruling recovery

Recovery store schema 9 adds `legacy_ruling_recoveries`. The owner migrates
stores 1–8 by adding an empty collection and retaining existing records.
An older store already containing this collection is refused. State document
schema 6 and checkpoint record versions remain unchanged. Older owner builds
cannot write schema 9; use the upgraded owner for every dispatch and reader.

Schema 10 adds no collection: it widens `dispatches[].role` to a seat. The
owner stamps a store at 1–9 and retains its records; a store at those versions
carrying a seat-named dispatch is unowned newer data and is refused without
writes. A reader pinned to 9 or lower reads a schema-10 store as newer and
takes its no-prior-state path rather than migrating it downward.

Existing schema-1 receipts stay in the ledger. Each carries `id`, `task`, `at`,
the actual operator `authorization` source/quote, `backup` path/SHA-256, a
`checkpoints` object mapping original checkpoint IDs to canonical JSON SHA-256
digests, and `grants_future_attempts: false`. Every referenced row retains its
complete original content and must read as version 2 after the owner's checkpoint migration, without `requested_by`.
Reading validates these bindings without fetching historical files. Altered,
new or overlapping citations do not inherit a receipt. Malformed or unsupported
recovery state is refused without writes.

The version-3 one-ruling read bound is unchanged: version-2 citations read as
written. The checkpoint writer still refuses another ruling for a task that
already has one, and correction limits are unchanged. No task identity, base,
assignment, attempt count, plan, evidence or authorization is removed or
invented. Receipts grant no corrections, review approvals or new dispatch
authority. There is no owner command that appends, deletes or downgrades these
records.

## Foreman Reset Record

`<canonical-state-path>.foreman-reset.json` is owned by
`skills/herdr-foreman/foreman/foreman_reset.py`, which is its only writer and
reader. It writes under the file's own state lock, never the main state lock.
`foreman-reset` appends a row and starts the deliverer. The reader
checks every field, and the `result` shape each `status` requires.
`foreman-reset-deliver` claims that row and finishes it.

Envelope: `{"schema_version": 2, "resets": [<row>, ...]}`, rows in append
order. A missing file means no prior reset. A file whose envelope carries an
integer `schema_version` above 2 was written by a newer build: a read takes it
as no prior reset, and a write refuses with `reset_record_newer`, leaving the
file untouched (`rules/stateful-artifacts.md` Migration Policy). A record that
is a link, cannot be read or parsed, fails any row's validation, or holds two
rows for one pane and stow is refused with `reset_record_unusable` and left
untouched. Only the deliverer that claimed a `delivering` row finishes it, and
an outcome that would not validate is refused before it is written. The
deliverer waits up to `CLAIM_LOCK_BUDGET_SEC` for the record lock, which
`foreman-reset` holds until it has saved the deliverer's identity.

Schema 2 adds `native_session` (#523). The owner migrates a schema-1 record
on read (`foreman_reset._migrate`): every row must first validate as a
schema-1 row, or the record is `reset_record_unusable` and untouched; each
row then gains `native_session: null`, a `delivered` result's
`schema_version` becomes 2, and the envelope becomes 2. The owner rewrites
the upgraded record on that read, under the record lock, `catch-up`'s read
included. A deliverer of the schema-1 build still running then reads a newer
record and cannot record its outcome: its row stays `scheduled` or
`delivering`, and once that process is gone catch-up names the
`foreman-reset-reconcile` command for it, as for any outcome the record
could not take.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer, always `2` | Row version |
| `pane_id` | string | The foreman's Herdr pane; with `stow`, the reset's identity |
| `stow` | string | The stow id the resume prompt names |
| `status` | one of `scheduled`, `delivering`, `delivered`, `failed`, `interrupted`, `reconciled` | `scheduled` → `delivering` → `delivered`; `failed` before any keystroke; `interrupted` after one; `reconciled` when the operator confirmed through `foreman-reset-reconcile` that the foreman resumed |
| `scheduled_at` | ISO-8601 string with timezone | The `foreman-reset` time |
| `options` | object with optional non-empty string `config` and `herdr_bin` | The non-default settings `foreman-reset` ran with; every resume prompt for this row carries them, including one finalized later by another process |
| `process` | `{"pid": integer, "identity": string}`, or null | The deliverer's process: its pid and a digest of its start time and command line (`supervision_runtime.process_identity`). A reused pid carries another identity. Null only on a `scheduled` row before its deliverer is identified, or on a `failed` row whose deliverer never started or was gone before identification. Every `delivering`, `delivered` and `interrupted` row carries one |
| `native_session` | `{"kind": "id" \| "path", "value": non-empty string}`, or null | The foreman's native session as `supervision-bind` recorded it (the binding's `identity` `kind` and `value`); `foreman-reset` refuses to schedule without one. Every keystroke of the clear command, extra Enters included, refuses unless `herdr pane get` still reports that session for the pane, finishing the row `failed` before any keystroke and `interrupted` after one, both with error `reset_session_changed` and `details.reason` `native_session_changed`. The clear starts a new session by design: once the composer confirms it consumed, the deliverer waits up to `CLEAR_SESSION_BUDGET_SEC` for Herdr to report a new session for the pane and pins it, and every keystroke of the resume prompt refuses unless the pane still holds the pinned session. The pin also requires the pane's foreground processes (`herdr pane process-info`) to be the ones the first keystroke found, compared by pid, start time and command line (`supervision_runtime.process_identity`); a new session under another process is a replacement, refused with `native_session_changed`. A pane that reports no new session in that budget finishes the row `interrupted` with `details.reason` `clear_session_unchanged`. A transcript path that cannot be resolved matches no session. Null means no session was recorded: the migration writes it on every schema-1 row, and `foreman-reset` never writes it. The reader accepts null on any row; a deliverer that claims a null row refuses before any keystroke |
| `result` | null, the delivery object, or the failure object | `scheduled` and `delivering` hold null. `delivered` holds exactly `{"schema_version", "pane_id", "stow", "agent", "cleared": true, "resume": {"landed": true, "started": true}}`, whose `schema_version`, `pane_id` and `stow` equal the row's. `failed` and `interrupted` hold exactly `{"error": string, "message": string, "details": object, "resume_prompt": string}`; `resume_prompt` is what the operator pastes. `reconciled` holds exactly `{"outcome": "delivered", "reconciled_at": ISO-8601 string}` |

One delivery attempt per pane and stow, never retried automatically. A
retry replays before every precondition the reset itself changes (stow
readiness, supervision work); reading the stow and supervision, and checking
the caller's pane, still come first. A `delivered` or `reconciled` row,
or a `scheduled` or `delivering` row whose exact process identity is still
alive, replays: each returns the recorded row with `replayed: true` and
starts nothing. A dead `scheduled` row is finalized `failed` (nothing
was typed), and a dead `delivering` row `interrupted` (typing may have
begun), each with the failure object. Every other row, `failed` or
`interrupted` whether recorded earlier or just finalized, is then refused
with `reset_ended` and its resume prompt, for operator recovery under
the Working Memory carve-out. The next
round resets from a new stow. A deliverer claims only the row carrying its
own process identity. A launch failure, or a deliverer already gone when
probed, finishes the row `failed`. A `foreman-reset` that dies between the
row's first save and the identity save leaves it `scheduled` with a null
`process`; the next read finds no live deliverer and finalizes it `failed`.
A deliverer that fails before its claim records its still-`scheduled` row
`failed` itself. A deliverer exits `reset_ended`, with the record path and
resume prompt, only when the row shows `failed` or `interrupted`; when the
record could not be updated it exits with that error instead.

The record is the durable blocker for a failed reset: `catch-up` reads it
through `foreman_reset.outstanding` and lists, ahead of the attention queue,
each pane whose latest reset ended `failed` or `interrupted`, or never reached
an outcome with its deliverer gone, and an unreadable record. A later
`delivered` or `reconciled` reset for the pane supersedes an older failure.

`foreman-reset-reconcile --pane <pane> --stow <stow> --outcome delivered|failed`
is the owner's repair for a row whose deliverer is gone: a `scheduled` row
(`failed` only, since nothing was typed), a `delivering` row (either outcome),
and an `interrupted` row the operator saw resume (`delivered` only): `failed` records the failure with the resume prompt built from the row's
own `options`, `delivered` records `reconciled`. An identical retry returns
the recorded row with `replayed: true`; a row that already ended any other
way, or whose deliverer is still running, is refused.
