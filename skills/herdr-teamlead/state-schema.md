# Foreman State Schema

Schemas for the cross-invocation artifacts owned by `herdr-teamlead`, per
`rules/stateful-artifacts.md`. The Python utility alone writes and migrates
`state.json`. The foreman maintains the separate Markdown task ledger; the utility
does not parse or update that document. The foreman drafts retrospective synthesis;
the utility alone records the saved notes and their separate index.

## Artifacts

| Path | Owner | Purpose |
| ---- | ----- | ------- |
| `$XDG_STATE_HOME/teamlead/state.json` (default `~/.local/state/teamlead/state.json`, override `--state FILE`) | `skills/herdr-teamlead/teamlead/state.py` and its `recovery.py` helper, within the same owner skill | Snapshots, append-only assignments, and audited task recovery |
| `$XDG_CONFIG_HOME/teamlead/config.json` (default `~/.config/teamlead/config.json`, override `--config FILE`) | the operator | Per-agent usage / clear commands; teamlead reads it and never writes it |
| `<task-reports-dir>/TASK-LEDGER.md` | `herdr-teamlead`, written by the foreman | Evidence-backed assignment acceptance and task completion across rounds |
| `<canonical-state-path>.retrospectives/` | `herdr-teamlead`, through its retrospective utility | Immutable retrospective notes, versioned index, and transition coverage |
| `<canonical-state-path>.foreman-reset.json` | `skills/herdr-teamlead/teamlead/foreman_reset.py` | One record per foreman round-boundary reset; see Foreman Reset Record below |

The JSON formats and utility contracts below apply to `state.json` and config.
The Markdown ledger has its own contract in Task Ledger below; adding it changes
none of the existing JSON record shapes or versions.
The retrospective sidecar also leaves `state.json` and assignment versions
unchanged. Its canonical state path is the expanded, resolved path selected by
`--state` or the existing default; separate state files have separate histories.

`skills/herdr-teamlead/config.example.json` is an example to adapt and commission before live tier use. Config schema 3
adds per-agent `capabilities`; schemas 1 and 2 remain readable without rewriting
the operator-owned file. Missing capabilities mean an empty list, never inferred
expertise. Capability entries are unique lowercase identifiers validated by
`teamlead/config.py` (`parse_capabilities`). Declare them from available skills,
tools and inspected evidence. The example leaves every capability list empty.
Config schema 2 added per-agent `tiers` and `launch_args`.
See `skills/herdr-teamlead/references/model-tiers.md` for billing evidence. A missing config is refused with the exact `cp` command to run. The
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

Plan schema 9 adds `pressure_headroom` and `de_escalated` to each entry in
`tiers` (#477). Writer: `plan`, from the measured snapshot. Reader: `apply`,
which recomputes each tier against the headroom the plan resolved with rather
than measuring again. An older plan carries neither field, reads as unmeasured
pressure, and resolves as it always did.

The optional `role_costs` key is the second:
`{"<role>": <number>}`, what one round in that seat is expected to
burn out of a worker's remaining headroom percentage. It overrides the
planner's own weights one role at a time, and a role it omits keeps the
default (see `skills/herdr-teamlead/references/round-setup.md`, Step 5).
A missing map means no overrides; a value that is not a non-negative finite
number is refused, naming the file and the role. `plan` is the only reader.

## Task Ledger

Choose one absolute task reports directory outside the shared checkout and
worker worktrees. Keep `TASK-LEDGER.md` there across fixes, releases, and resumes.
Record its absolute path in the saved task authorization context and the foreman's
handoff before first dispatch. Do not move or delete it during worktree cleanup.
It replaces the informal round log, not the utility's dispatch/recovery ledger.

The Markdown document's frontmatter contains `schema_version: 1`, the stable
`task`, full original `base_revision`, and the absolute `dispatch_state` path
of the utility ledger. Each appended event is a Markdown section with these
required fields; unavailable values are the literal `unknown`, never guesses:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer, always `1` | Row version |
| `pane_id` | string | The foreman's Herdr pane; with `stow`, the reset's identity |
| `stow` | string | The stow id the resume prompt names |
| `status` | one of `scheduled`, `delivering`, `delivered`, `failed`, `interrupted` | `scheduled` → `delivering` → `delivered`; `failed` before any keystroke; `interrupted` after one |
| `scheduled_at` | ISO-8601 string with timezone | The `foreman-reset` time |
| `pid` | integer, or null | The deliverer's process id. Null only between the row's first save and the deliverer's start, both under the record lock |
| `result` | null, the delivery object, or an error object | Null until finished. `delivered` holds `{"schema_version", "pane_id", "stow", "agent", "cleared", "resume": {"landed", "started"}}`. `failed` and `interrupted` hold the error's `{"error", "message", "details"}`, or `{"error": "deliverer_lost", "pid"}` when a scheduled deliverer vanished |

A `delivered` row replays forever, and a retry replays before any new-reset
precondition is checked. A `scheduled` or `delivering` row replays while its
process is alive. A `scheduled` row whose process is gone is marked `failed`
and scheduled again, since nothing was typed. A `delivering` row whose process
is gone, and any `interrupted` row, is refused, because the pane may already
be cleared. A new stow starts
a new reset. A deliverer claims only the row carrying its own `pid`. A
`failed` row allows a new row for the same pane and stow. A missing file is
no prior reset. An unreadable file, or one with another `schema_version`, is
refused and left untouched.
