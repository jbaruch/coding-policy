# Team-Lead State Schema

Schema for the cross-invocation state the `herdr-teamlead` skill's Python utility
writes and reads, per `rules/stateful-artifacts.md`. The utility is the sole
owner: it writes every record and is the only thing that may change their shape.

## Artifacts

| Path | Owner | Purpose |
| ---- | ----- | ------- |
| `$XDG_STATE_HOME/teamlead/state.json` (default `~/.local/state/teamlead/state.json`, override `--state FILE`) | `skills/herdr-teamlead/teamlead/state.py` | Headroom snapshots plus the append-only role-assignment ledger |
| `$XDG_CONFIG_HOME/teamlead/config.json` (default `~/.config/teamlead/config.json`, override `--config FILE`) | the operator | Per-agent usage / clear commands; teamlead reads it and never writes it |

`skills/herdr-teamlead/config.example.json` is the ship-ready config to copy into
place. A missing config is refused with the exact `cp` command to run. The
optional `idle_markers` / `working_markers` per-agent keys carry the footer
signatures the stale-state probe reads; an agent with neither is never probed.
`slash_delivery` picks how that worker's slash commands go out, `paste` or
`type`; `dialog_next_tab_keys` names the keys that cycle a usage dialog's tabs;
`composer_glyph`, `composer_ignore_dim`, `composer_placeholders`, and
`recover_keys` drive the consumed-command check, the ghost-text and placeholder
exemptions, and the guarded one-shot recovery of a stuck composer.
`model_label` is cosmetic: it names the model on the worker's pane after a
dispatch. All are documented in
`skills/herdr-teamlead/references/herdr.md`.

`window_group` names the usage window an agent shares with other agents: two
workers authenticating as one subscription declare the same value, `measure`
copies it onto each record (snapshot `schema_version` 2), and `plan` charges a seat's cost against every
worker in that window. An agent that declares none has a window to itself.

Two top-level config keys are not per-agent. The optional `judge` key pins the
judge seat, and `plan` echoes it back as the document's `judge` object when the
seat is planned, so the worker's launch argv is built from the config rather
than by hand: `{"agent": <name>, "model": <id>, "effort": <level>}`, where
`effort` is one of `low`, `medium`, `high`, `xhigh`, `max`, or absent for a
model that accepts no effort flag. The planner never ranks that seat and never
gives the pinned worker another one.

The optional `role_costs` key is the second:
`{"<role>": <number>}`, what one round in that seat is expected to
burn out of a worker's remaining headroom percentage. It overrides the
planner's own weights one role at a time, and a role it omits keeps the
default (`DEFAULT_ROLE_COSTS` in `skills/herdr-teamlead/teamlead/planner.py`).
A missing map means no overrides; a value that is not a non-negative finite
number is refused, naming the file and the role. `plan` is the only reader.

## State Record Format

```json
{
  "schema_version": 2,
  "snapshots": ["<measure output>, oldest first, ring capped at 20"],
  "assignments": [
    {
      "schema_version": 2,
      "at": "2026-09-01T21:00:00+00:00",
      "role": "developer",
      "agent": "grok",
      "status": "applied"
    }
  ]
}
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `2`. Bumped on any shape change |
| `snapshots` | array | Whole `measure` documents, oldest first; the ring holds the last 20 |
| `assignments` | array | Append-only ledger of who held which role |
| `snapshots[].schema_version` | integer | Currently `2`. Version 2 added `window_group` to every agent record |
| `snapshots[].agents[].window_group` | string | The usage window this agent shares with others; absent on a version-1 snapshot, which reads as a window of its own |
| `assignments[].schema_version` | integer | The row's own version, stamped on write |
| `assignments[].at` | string | ISO-8601 timestamp, from `--now` or the CLI's clock |
| `assignments[].role` | string | The role handed out |
| `assignments[].agent` | string | The agent that received it |
| `assignments[].status` | string | `applied`, `sent_but_not_started`, or `unknown` |

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

Each snapshot is one `measure` document: `schema_version`, `measured_at`, an
`agents` object keyed by agent name (`kind`, `state`, `herdr_state`,
`state_source`, `pane_id`, `windows`, `credits`, `plan`, `headroom_pct`,
`skipped`), and `failed_agents`. `headroom_pct` is the minimum `remaining_pct`
across that agent's windows. `state_source` is `herdr` or `probe`, naming which
signal decided `state`; `herdr_state` carries what herdr claimed. `plan` is an
informational plan name and never feeds headroom.

## Writer / Reader Contract

- **Writer** — `measure` appends a snapshot; `apply` appends one ledger entry
  per successful hand-off, so an interrupted round still records exactly what
  was sent. Writes are atomic: temp file in the same directory, `fsync`,
  `os.replace`.
- **Readers** — `plan` reads the newest snapshot plus the ledger (role history
  breaks a headroom tie), and the config's `role_costs` for its seat weights;
  `state` prints the document. Neither writes.
- **Absent state** — a first run has no file. Every reader treats that as no
  prior state and continues; `plan` still requires a snapshot, passed with
  `--snapshot` when the state file holds none.

## Migration

Only the owner migrates, and it reads a version in one of three directions.

- **Older** — the document, or a single ledger row, is walked up the
  `MIGRATIONS` / `RECORD_MIGRATIONS` chain in
  `skills/herdr-teamlead/teamlead/state.py`, keyed by the version being upgraded
  from, and the upgraded file is rewritten in place. A document or row carrying
  no `schema_version` reads as the pre-versioning version `0`, which is what
  gives the chain a step below `1`. The `1 → 2` step stamps `status: unknown`
  on every row written before the field existed.
- **Newer** — this build is the lagging reader, not the migrator. The caller
  gets an empty document in memory, the file on disk is left exactly as found,
  and a warning goes to stderr. A single row stamped ahead of this build makes
  the whole document unusable rather than being dropped, so the next write
  cannot lose it.
- **Corrupt** — unparseable JSON, a non-object document, a non-array field, a
  non-object row: no usable prior state, treated exactly like the newer case.
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
