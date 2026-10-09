# Disposable Probe Recovery

Read this reference when normal measure reports a retained disposable probe.
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
It rechecks before closing only that owner-created probe. No usage command,
assignment, trust choice or recovery key is sent.

Exit 0 emits the owner record with `status: closed` and actual `closure`;
replaying a closed record returns `replayed: true` without native calls. Cleanup
refusals exit 1 with structured recovery naming the same command and preserve
pending ownership. Missing records, mismatched config paths or worker config,
and unsupported/unreadable gate data exit 1 with an actionable usage/state
diagnostic, without a recovery object or native cleanup. Drafts,
working/blocked targets, changed/missing identity or tier,
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

Document schema 2 is `{schema_version: 2, records: [...]}`. Each schema-2 row
contains `at` (timezone-qualified original observation), `agent`, `pane_id`,
`worker_kind`, native `kind`, original canonical `config_path`, `window_group`
(empty when unshared), selected
`tier`, `config_sha256` (immutable SHA-256 of the original worker's exported
configuration including its billing window), original raw native-session observation `native` (object or explicit
null), original verified `process`, `status` (`pending` or `closed`), and
`closure` (null until actual closure), and `phase` (`startup` or `cleanup`).
Startup rows retain pre-input native dialogs; cleanup rows retain failed
disposable cleanup after startup or usage. Original identity/tier fields remain
unchanged on resolution. Closed rows preserve the recovery history.
Resolution requires the current worker config to match that digest before any
native call; editing its launch/composer settings cannot authorize cleanup.

Writer creates a row only from a proved owner-created first start, for a
pre-input startup-dialog refusal or unproved later cleanup. A pending row
refuses another probe for its worker kind/shared window until the guarded
owner command records closure. Cleanup rows preserve the original binding,
not a replacement observed during failure. Reprove that full binding before
each usage/recovery/Enter or dialog-tab input, usage-dialog dismissal and pane
cleanup. Changed bindings authorize none of them.
Missing startup bindings also refuse cleanup; preserve that surface for
read-only owner inspection rather than closing a same-name/pane replacement.
No assignment input occurs under either phase; a cleanup row may have usage
input. A failed startup-dialog retention write leaves
ordinary owned pre-input cleanup in force; it cannot advertise a durable gate.
Failed cleanup retention writes fail visibly and preserve the surface, never
claiming persistence or permitting an automatic retry.

Readers are those two owner commands. Missing documents mean first use;
The owner validates and migrates schema 1 to schema 2 on read, stamps original
rows `phase: startup`, and atomically rewrites the gate. Original proof, status,
config digest and closure history stay unchanged. Corrupt or unsupported documents/rows remain unchanged and refuse
measurement and cleanup with a restore-evidence diagnostic.

This is a gate-store exception to the newer-record no-prior-state fallback in
`rules/stateful-artifacts.md`: an unresolved row prevents another probe, so
discarding unsupported evidence would lose that guard. A lagging reader
refuses rather than initializing an empty store. The owner migrates every
older version it accepts; schema 1 is the only older supported shape.
