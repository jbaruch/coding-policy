# Recover an Owner-Closed No-Work Assignment

Run `recover-no-work --record FILE` through the normal foreman entrypoint with
its original `--state`. This owner operation records permission for one fresh
initial developer assignment after an unavailable assignment's pane was already
closed. It sends no input, launches no worker, closes no pane, and rewrites no
transport or assignment history. Native work may have started before the stall;
this is not the unsent-bootstrap recovery path.

Supply a JSON object with these fields:

| Field | Input |
| --- | --- |
| `dispatch` | Original applied developer dispatch ID |
| `reason` | Recovery reason |
| `authorization` | Existing task authorization as `{source, quote}` |
| `evidence` | Absolute path to the complete saved spent `wait-report` JSON |
| `closure` | Absolute path to the saved successful `close-member` JSON |
| `ledger` | Absolute path to the original task ledger cited by that closure |

Keep the saved artifacts unchanged. The owner checks their bindings against the
registered task, original dispatch and resolved supervision enrollment, then
checks the current checkout and runtime. The accepted proof, chronology and
retry predicates belong to `skills/herdr-foreman/foreman/no_work_recovery.py`.
A missing runtime or report alone is insufficient. No recovery is inferred from
a terminal status, a clean unrelated checkout, or a caller's `not_sent` label.
If the saved closure or original evidence is missing, retain the history and
recover those artifacts; do not manufacture an unsent draft or bootstrap error.

Success emits a schema-1 receipt with its source dispatch `id`, original task,
assignment index, input, evidence digests, supervision binding and `retries`.
The latter lists replacement dispatch IDs reserved against this receipt. An
identical call returns the durable receipt, including after the replacement has
started; replay grants no additional permission. Conflicting input for that
identity refuses. Exit 1 reports evidence, state, Git or runtime errors with an
actionable diagnostic; exit 2 is CLI usage.

After recovery, use normal `plan` and `apply` for the **same task**, with the
original round/oracle context and without `--fix-round`. Use a fresh worker
identity and report path. Keep the original COMMON and developer brief content,
changing only the report destination. Planning selects the currently supported,
capable tier through its existing gates; recovery grants no model override.
Apply rechecks the recovery evidence before creating a replacement pane and
binds its reservation durably before input. Unknown transport outcomes block
another attempt. A proved pre-send failure follows normal transport recovery.

The task retains its original base, scope, oracle, contributor exclusions,
correction allowance, attention gates and independent review/release gates.
The old applied dispatch remains applied, with its actual started/native-session
facts. A no-work retry is another transport of the initial assignment, never a
source correction or evidence of acceptance. Later source corrections use the
normal cumulative fix sequence. This command covers initial assignments only;
partial, unknown, committed or reported work follows its existing recovery route.

The receipt and migration contract is in
`skills/herdr-foreman/state-schema.md` under Closed No-Work Recovery.
