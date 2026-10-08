# Startup Probe Recovery

Read this reference when normal measure reports a retained startup probe.
Runtime Dialogs in `skills/herdr-foreman/references/herdr.md` owns trust and
permission decisions; the probe owner never decides them from UI text.

## Owner Command

Use the installed launcher resolved in SKILL.md with the same config and state:

```text
resolve-probe --state <original-state-path> --config <original-config-path> --agent <recorded-probe-name>
```

The agent name comes from the measure receipt, not a current assignment. After
the foreman resolves the actual native dialog within existing task authority,
the command checks the original name, kind, pane, native-session observation,
foreground process and tier, then proves idle/done and an empty composer.
It rechecks before closing only that no-input probe. No usage command,
assignment, trust choice or recovery key is sent.

Exit 0 emits the owner record with `status: closed` and actual `closure`;
replaying a closed record returns `replayed: true` without native calls. Exit 1
emits structured recovery, preserves pending ownership, and names the same
command. Drafts, working/blocked targets, changed/missing identity or tier,
unsupported config, unreadable evidence and cleanup failures do not authorize
closure. An absent agent permits cleanup only when its recorded pane is absent
or holds only its shell. The recorded absence of a first-start native session
must still match; a later session is not original proof.

Repeat normal measure after successful resolution. Until then it reports
unknown capacity for the affected worker kind/billing window without launching
another probe; independent windows can still be measured. This recovery creates
no dispatch, task acceptance, quota estimate or correction attempt.

## Durable Schema

Owner and sole writer: `herdr-foreman`, through
`skills/herdr-foreman/foreman/probe_recovery.py`. The selected canonical state
owns an adjacent `<state-path>.probes.json`; `measure` and `resolve-probe` hold
the canonical state's existing transaction lock through observation and writes.
Writes use the existing atomic state writer.

Document schema 1 is `{schema_version: 1, records: [...]}`. Each schema-1 row
contains `at` (timezone-qualified original observation), `agent`, `pane_id`,
`worker_kind`, native `kind`, original canonical `config_path`, `window_group`
(empty when unshared), selected
`tier`, original raw native-session observation `native` (object or explicit
null), original verified `process`, `status` (`pending` or `closed`), and
`closure` (null until actual closure). Original identity/tier fields remain
unchanged on resolution. Closed rows preserve the recovery history.

Writer creates a row only after a proved owner-created first start and
pre-input startup-dialog refusal. Persistence precedes retaining the surface.
No assignment or usage input occurred under that row. A failed write leaves
ordinary owned pre-input cleanup in force; it cannot advertise a durable gate.

Readers are those two owner commands. Missing documents mean first use;
schema 1 is the first published shape and has no older supported schema to
migrate. Corrupt or unsupported documents/rows remain unchanged and refuse
measurement and cleanup with a restore-evidence diagnostic.

This is a gate-store exception to the newer-record no-prior-state fallback in
`rules/stateful-artifacts.md`: an unresolved row prevents another probe, so
discarding unsupported evidence would lose that guard. A lagging reader
refuses rather than initializing an empty store. The owner migrates every
older version it accepts; this first version accepts none.
