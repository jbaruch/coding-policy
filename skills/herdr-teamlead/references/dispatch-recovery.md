# Dispatch and Wait Recovery

## Dispatch outcomes

Each outcome names where the round goes next. Only a dispatched worker can
produce a report, so Step 11 waits on exactly the roles that landed here.

- **Exit 0** — every role was dispatched. Proceed to Step 11.
- **Non-zero with a busy target** — the whole round was refused before any
  keystroke went out. Nothing was dispatched, so there is nothing to wait for:
  wait for that worker to reach idle and re-run this step, or re-run it with a
  plan that omits the busy worker. Do not go to Step 11.
- **Non-zero with `"status": "sent_but_not_started"`** — the message was sent
  and no turn began for that role. Read that worker's pane; do not re-dispatch
  on top of it. Go to Step 11 for the roles whose records say `started`, and
  treat this role as producing no report this round.
- **A worker failed on its clear command** — its assignment was never sent, and
  the round is short that role. Go to Step 11 for the rest; re-dispatch this one
  by re-running this step for that role alone once its pane is clear.
- **A refusal saying the screen did not change** — the clear was unconfirmed;
  no brief was sent to that worker. Read the pane and clear it by hand.
  Re-run with `--no-clear` once the pane shows a fresh session. Fresh fix
  rounds that require an automatic clear must re-run without that flag.
  `--no-clear` records `cleared: false, clear_reason: hand`;
  `--retain-context` records `cleared: false, clear_reason: retained`.
  Never retain the previous task's context or retain across a role change.
- **A refusal naming an unaccounted composer** — the worker's input line holds
  text the lead did not send. Inspect the recorded dispatch status before
  retrying: a refusal during send confirmation can leave an uncertain outcome. Read the
  pane and clear it by hand, or re-run this step with `--allow-recovery` once
  you know whose text it is. Codex sends no recovery key at all: its clear key
  exits an idle Codex.
- **A refusal the message does not cover** — report it verbatim and finish
  here. A dispatch nobody understands is not a round to wait on.
- `--dry-run` prints the context choice and commands without Herdr calls or
  state writes. It reads current recovery bounds but proves neither retained
  history nor live readiness. An older ledger requires `teamlead state` to
  perform its owner migration before this preview.
  It dispatches nothing; finish here after reading it.
- Each confirmed hand-off relabels that worker's pane with the work it took.
  `--task <label>` puts the round's task in the label. A hand-off that never
  started is left unlabelled.

The delivery mechanics behind those outcomes — composer confirmation, recovery
keys, ghost text, the rejection strings, the settle knobs — are in:

```text
skills/herdr-teamlead/references/herdr.md
```

The prompt text, the refusal predicate, and every constant are the utility's
own contract; see `skills/herdr-teamlead/teamlead/assign.py`.

Proceed to Step 11 with the roles that were dispatched.

## Owner-managed recovery

Use `bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh`
for the commands below, with the same `--state FILE` throughout. Every owner
mutation takes `--record FILE` containing a JSON object and optional `--now`.
Success prints the recorded object on stdout, exit 0. Exit 1 prints an
actionable JSON error on stderr; resolve that cause before proceeding. Do not
edit state.json or a prior assignment row by hand.

`authorization` is always `{"source": "<operator message reference>", "quote":
"<their actual words>"}`. Record existing authorization when it covers the
action. Never invent it, treat silence as consent, or convert a general shipping
instruction into permission to exceed an exhausted correction budget.

| Command | Record fields | Continuation |
| --- | --- | --- |
| `task` | `task`, original full `base_revision`, `scope`, `allowed_paths`, `authorization` | Register once before initial development; for legacy history, recover these facts from the original task and brief. Continue the same task. |
| `checkpoint` | unique `id`, `task`, concrete `defect`, `previous_attempts`, `progress`, `change_in_approach`, absolute `judge_report` | Requires the configured pinned judge's completed ruling after the latest developer attempt. Implementation waits for the bounded operator decision. |
| `authorize-corrections` | unique `id`, `task`, `checkpoint`, `scope`, `allowed_paths`, positive `additional_fixes`, `authorization`; optional `supersedes` | Store an explicit bounded approval once. Continue while it covers the next attempt; do not ask again within those bounds. A changed decision names the active plan in `supersedes`. |
| `record-report` | `dispatch`, full `head_revision`, `verdict` (`blocking` or `approved`), `review_mode` (`full` or `scoped`), independent `reviewer`, absolute `report`, `changed_paths` | Read the report in full and verify the VCS diff first. The command binds its bytes and stated head to the dispatch; it does not establish the tester, CI, external-review, or release gates. |
| `recover-context` | `task`, original `assignment_index`, `reason`, `authorization`, absolute `evidence` | For the latest confirmed developer row with null native-session proof. Records a live observation separately and permits the next fresh handoff. The original null stays null. |
| `reconcile` | `dispatch`, `outcome` (`applied` or `not_sent`), `reason`, `authorization`, absolute `evidence` | Resolve an interrupted send from actual evidence and an idle/done live worker. `applied` appends recovered assignment evidence without fabricating contemporaneous session proof; `not_sent` permits a transport retry. |

`allowed_paths` contains repository-relative paths or globs. Preserve the
original task and base across every approval. Read and verify the source diff
against the proposed scope; neither a worker's claim nor a receipt alone proves
the source change. Preserve the prior reports with their original bytes.

For an approved extra correction, pass the same `--task`, actual `--fix-round`,
`--correction-plan ID`, and `--work FILE` to plan and apply. Work JSON contains
`base_revision`, exact approved `scope`, repository-relative `paths`, and a
non-empty array of blocking `findings`. The owner validates the allowance
before tier selection and dispatch. Later authorized corrections keep the true
number and fresh top-tier behavior. Each subsequent correction within a plan
requires the preceding attempt's actual blocking review, recorded through
`record-report`. Approved or absent findings do not justify another attempt.

At budget exhaustion, return to the judge and a new concrete checkpoint.
Changed scope or a changed operator decision requires new explicit bounds;
unchanged in-scope work reuses its approval. Do not rename the task or reset
the counter. Every changed tip still needs full independent reviewer and
tester reports before release, plus the release skill's external reviews and
CI. A scoped recheck cannot replace that verification.

## Fresh sessions and interrupted sends

A normal cleared release assignment preserves the previous developer row and
permits its next early developer fix in a fresh session. Register the task's
original metadata if legacy history lacks it, then omit `--retain-context`
and `--no-clear`. Include the release result, current findings, prior reports,
original base, and cumulative fix count in the fresh brief. No additional
context-change permission is needed. The following fix may retain that new
session when the normal same-role continuity preconditions hold.

Fresh developer dispatch correlates the official native session after the
first prompt, including delayed IDs. A stale pre-clear identity, changed pane
or session, malformed identity, or exhausted correlation leaves null proof.
The confirmed assignment remains recorded; wait for its report. Use
`recover-context` for that missing proof only after collecting the actual
evidence and applicable operator recovery authorization. Its later live
observation never retroactively proves the original session.

Labelled dispatches persist a reservation before clearing or relaunching, and
mark sending before the assignment can enter the terminal. Confirmed results
are saved before cosmetic pane labels. `--dispatch-id` supplies a stable
caller identity; otherwise the owner derives it from the task, role, worker,
count, context choices, and brief bytes. Identical completed retries return
`replayed: true` without Herdr calls or another attempt. Changed inputs under
the same explicit identity refuse.

An interrupted reservation or send holds its slot. `sent_but_not_started`
also remains uncertain; idle alone cannot establish whether it happened.
Inspect the live worker and actual report or transport evidence, then use
`reconcile`. Never resend, delete the reservation, or consume another attempt
to work around uncertainty. Reconciliation records its later observations
separately and preserves any original unconfirmed assignment row.

`teamlead status` prints implementation state, confirmed fixes, active plan,
and remaining allowance. `waiting_for_operator` explicitly pauses implementation
while its checkpoint awaits approval; an audit worker may still be active.
`dispatch_outcome_unknown` requires reconciliation, and
`judge_checkpoint_required` requires the next exhausted-budget checkpoint.
Neither an active worker nor a dispatch receipt proves that implementation or
release has finished.

Owner commands serialize access with a live OS lock. A competing command
refuses before dispatch; wait for the process holding the transaction. The
lock file can remain after exit; do not delete it to bypass an active lock.

## Wait outcomes

- **Exit 0** — the report is there. Continue to the next worker, then Step 12.
- **Exit 1** — the budget ran out. Read the pane with
  `herdr agent read <name> --source visible`. If the worker is still working,
  re-run this step for it: the script's own budget applies again, never a
  number chosen here. Otherwise go to Step 12 recording that it produced no
  report.
- **Exit 2** — a tool failure. Report the message verbatim and finish here; the
  round has no reliable view of any worker.
- **Exit 4** — the report file exists and the worker reads `idle` or `done` on
  consecutive polls, but the pane never showed the marker whole. This is not
  delivery. Read the live state with
  `herdr agent get <name>` and take the first continuation that applies:
  - The command fails — report its message verbatim and finish here, as for
    exit 2.
  - The state is `blocked` or `working` — re-run this step for that worker
    once. Other exits from that re-run take their documented branches. A second
    exit 4 is terminal: record the worker as producing no report and
    continue to the next worker.
  - The state is `idle` or `done` — record the worker as producing no report
    and continue to the next worker. Never re-dispatch on top of it. The
    marker may be wrapped, quoted, absent, or identify another attempt. Do
    not join rows or use a matching filename as proof. Step 7 requires a fresh
    report destination and bounds its length; narrow panes can still wrap it.
- **Exit 3** — the worker is blocked at an approval or question dialog,
  confirmed across two reads and the pane. Read the dialog with
  `herdr pane read <pane-id> --source visible`, relay its text to the operator
  verbatim, and stop the round for that worker. You never answer it: the
  operator does. Resume only once `herdr agent get <name>` reports a state
  other than `blocked`, then re-run this step for that worker.
- **Exit 5** — `reason: terminal_provider_refusal` identifies an unavailable
  attempt, with `found: false`. Record the missing report and tell the operator;
  every review/release gate remains unsatisfied. Do not automatically retry,
  rephrase, switch providers/models, reconstruct withheld output, or synthesize
  a report. Continue waiting on other dispatched workers; the operator decides
  how to handle the unavailable role under the existing rules.

`wait-report.sh` owns refusal confirmation; see its header and
`confirmed_provider_refusal`. Missing terminal evidence keeps the ordinary
wait. Herdr 0.8.2's bundled API schema has no dedicated provider-refusal
outcome; the watcher uses its documented pane/state surfaces. Synthetic
fixtures verify decisions, not live provider behavior or production elapsed
time.

Proceed to Step 12 once every dispatched worker has been waited on, or once you
have recorded which of them produced no report.
