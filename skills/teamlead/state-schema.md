# Team-Lead State Schema

Schema for the cross-invocation state the `teamlead` skill's Python utility
writes and reads, per `rules/stateful-artifacts.md`. The utility is the sole
owner: it writes every record and is the only thing that may change their shape.

## Artifacts

| Path | Owner | Purpose |
| ---- | ----- | ------- |
| `$XDG_STATE_HOME/teamlead/state.json` (default `~/.local/state/teamlead/state.json`, override `--state FILE`) | `skills/teamlead/teamlead/state.py` | Headroom snapshots plus the append-only role-assignment ledger |
| `$XDG_CONFIG_HOME/teamlead/config.json` (default `~/.config/teamlead/config.json`, override `--config FILE`) | the operator | Per-agent usage / clear commands; teamlead reads it and never writes it |

`skills/teamlead/config.example.json` is the ship-ready config to copy into
place. A missing config is refused with the exact `cp` command to run. The
optional `idle_markers` / `working_markers` per-agent keys carry the footer
signatures the stale-state probe reads; an agent with neither is never probed.
`slash_delivery` picks how that worker's slash commands go out, `paste` or
`type`; `dialog_next_tab_keys` names the keys that cycle a usage dialog's tabs;
`composer_glyph`, `composer_ignore_dim`, `composer_placeholders`, and
`recover_keys` drive the consumed-command check, the ghost-text and placeholder
exemptions, and the guarded one-shot recovery of a stuck composer. All are documented in
`skills/teamlead/references/herdr.md`.

## State Record Format

```json
{
  "schema_version": 1,
  "snapshots": ["<measure output>, oldest first, ring capped at 20"],
  "assignments": [
    {
      "schema_version": 1,
      "at": "2026-09-01T21:00:00+00:00",
      "role": "developer",
      "agent": "grok"
    }
  ]
}
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `1`. Bumped on any shape change |
| `snapshots` | array | Whole `measure` documents, oldest first; the ring holds the last 20 |
| `assignments` | array | Append-only ledger of who held which role |
| `assignments[].schema_version` | integer | The row's own version, stamped on write |
| `assignments[].at` | string | ISO-8601 timestamp, from `--now` or the CLI's clock |
| `assignments[].role` | string | The role handed out |
| `assignments[].agent` | string | The agent that received it |

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
  breaks a headroom tie); `state` prints the document. Neither writes.
- **Absent state** — a first run has no file. Every reader treats that as no
  prior state and continues; `plan` still requires a snapshot, passed with
  `--snapshot` when the state file holds none.

## Migration

Only the owner migrates, and it reads a version in one of three directions.

- **Older** — the document, or a single ledger row, is walked up the
  `MIGRATIONS` / `RECORD_MIGRATIONS` chain in
  `skills/teamlead/teamlead/state.py`, keyed by the version being upgraded
  from, and the upgraded file is rewritten in place. A document or row carrying
  no `schema_version` reads as the pre-versioning version `0`, which is what
  gives the chain a step below `1`. Adding a `1 → 2` step later is one more
  entry in each table.
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

- A snapshot is a last-seen reading, never ground truth. `plan` may run off a
  stale snapshot deliberately; planning has no side effects.
- `apply` never trusts a snapshot for an agent's lifecycle state. It re-reads
  the live agent through `herdr agent get` and refuses a `working` or `blocked`
  worker before sending a single keystroke.
