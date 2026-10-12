# Disappeared Codex Bootstrap Recovery

`reconcile-bootstrap --record FILE` records a transport failure and retires an
owned shell after Codex exits during `account/read` TUI bootstrap. It sends no
input, starts no replacement, changes no capabilities, and grants no correction
or retry allowance. Run through the normal `foreman` entrypoint with the original
`--state`; the command holds that owner's transaction lock.

The record requires `dispatch`, `reason`, `authorization: {source, quote}`, and
`evidence` (absolute path to the saved visible pane text). Authorization cites the
existing task decision. The foreman checks that the saved evidence is the original
failure-only snapshot, taken before any shell interaction. Never replace it with
a later snapshot containing other work. A missing name or report alone is not
evidence that no turn began.

The saved text and first live pane observation must contain the complete frozen
assignment beneath the literal `Unsent draft:` heading, immediately followed by
`Error: account/read failed during TUI bootstrap:`. Terminal wrapping is accepted.
The owner recomputes the prompt from its original role, COMMON, brief and tier,
then checks its recorded prompt hash. Keep those frozen files and the saved pane
text unchanged through recovery.

Normal assignment-scoped Codex apply now saves its original root shell and
verified process before prompt input. Recovery requires that original pane and
root shell, a sole-shell foreground, no registration on the pane, absent original
agent, explicit null native-session observations before send, no started result,
no other worker history, and no enrolled report. Recorded supervision observations
of native work or a report also refuse recovery, even if later reads fail. An API failure is not absence.
All these checks run again before cleanup. The first live pane must match the
saved failure-only snapshot after ANSI removal. Its digest is persisted and must
remain unchanged before every close; an appended shell command or output
preserves the surface even when the old draft remains. If the original snapshot
is unavailable or its provenance is uncertain, preserve the pane and reconcile
the unknown work; a newly captured snapshot cannot establish the missing history. A changed pane, native session, report,
foreign process or unknown transport preserves the surface.

For older dispatches, optional `origin_evidence` names an absolute path to an
archived **pre-exit** `herdr pane process-info` JSON object (`result` and `process_info`
envelopes are accepted). It must carry `pane_id`, `shell_pid` and one `foreground_processes`
entry whose `pid` and `argv` equal the owner's original verified Codex process.
The owner imports that receipt only after checking the current surface. A
post-exit shell snapshot, pane title or agent name cannot substitute for this
archive. Without original shell provenance, recovery refuses; preserve the pane
and obtain actual launch evidence. Never add a shell PID by editing owner state.

The owner durably records `outcome: not_sent` before cleanup. Pending dispatches
also receive the ordinary not_sent reconciliation, observed state `absent` and
null native session. A legacy `applied` dispatch is eligible only with explicit
`started: false` and no native/session/report proof. Its assignment, result,
status and correction count remain historical facts; the separate bootstrap
receipt records the transport correction. It does not make that applied row
replayable or create retained context. Consult the owner receipt when recording
the unavailable assignment in the task ledger; no report or approval is supplied.

Success emits the versioned receipt plus `outcome: not_sent`,
`retry_authorized: false`, `history_preserved: true` and `closure.closed: true`.
Exit 1 reports evidence, state or Herdr errors; exit 2 is CLI usage. If cleanup or
supervision persistence fails, replay the identical record. Handle and acknowledge
pending supervision observations through their existing owner gate before resolution. The transport receipt
survives; replay retries only cleanup and resolution. A completed receipt replays
without another close. Preserve unknown or sent work, and use normal planning
and authorization gates for any later assignment.
