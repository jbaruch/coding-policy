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
`type`; `dialog_next_tab_keys` names the keys that cycle a usage dialog's tabs.
Both are documented in `skills/teamlead/references/herdr.md`.

## State Record Format

```json
{
  "schema_version": 1,
  "snapshots": ["<measure output>, oldest first, ring capped at 20"],
  "assignments": [
    { "at": "2026-09-01T21:00:00+00:00", "role": "developer", "agent": "grok" }
  ]
}
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `1`. Bumped on any shape change |
| `snapshots` | array | Whole `measure` documents, oldest first; the ring holds the last 20 |
| `assignments` | array | Append-only ledger of who held which role |
| `assignments[].at` | string | ISO-8601 timestamp, from `--now` or the CLI's clock |
| `assignments[].role` | string | The role handed out |
| `assignments[].agent` | string | The agent that received it |

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

- Only the owner migrates. A file whose `schema_version` is anything other than
  `1` is refused with a `mv` command to move it aside, never silently
  reinterpreted as the current shape.
- Bump `schema_version` for any shape change; never repurpose a field.
- The skill and the utility ship in the same plugin version, so writer and
  readers move together — `rules/stateful-artifacts.md` Cross-Pipeline Schema
  Bumps does not apply here.

## Hints, Not Authority

- A snapshot is a last-seen reading, never ground truth. `plan` may run off a
  stale snapshot deliberately; planning has no side effects.
- `apply` never trusts a snapshot for an agent's lifecycle state. It re-reads
  the live agent through `herdr agent get` and refuses a `working` or `blocked`
  worker before sending a single keystroke.
