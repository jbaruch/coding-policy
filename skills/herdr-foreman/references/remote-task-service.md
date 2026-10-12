# Remote Task Lifecycle Service

The supported boundary is the Python sidecar in
`skills/herdr-foreman/foreman/remote_service.py`. It adds caller lifecycle
operations to the attested owner in
`skills/herdr-foreman/references/remote-context.md`. The policy foreman remains
the sole coordinator. No transport operation plans, dispatches, kills a worker,
changes a model, grants a correction allowance or reconciles a Herdr effect.

## Trusted Bootstrap and Transport

The authenticated controller host loads the policy and constructs the existing
`RemoteForemanOwner` using trusted configuration and its independent live lease
reader. It explicitly initializes the original private store on first use.
On restart it reopens that same store, state path, receipt and policy bytes.
Neither construction nor initialization is a caller method.

The supported sidecar entry is `serve_stdio(owner, authorize)`. The trusted
bootstrap propagates its returned process exit code. The equivalent embedded
entry is `RemoteTaskService(owner, authorize).call(envelope)`;
`serve(service, reader, writer, diagnostics)` supports explicit text streams.
These are library entry points for the consuming host's bootstrap, not a generic
CLI accepting a module path, executable, shell command or SSH configuration.

A Node host launches its fixed, operator-configured Python bootstrap with argv
and pipes, with shell interpretation disabled. It sends UTF-8 JSON lines and
reads one JSON response per line. It need not construct Herdr/SSH commands or
import owner internals. The host owns authentication, controller lifetime,
foreman scheduling, task authority and final delivery to its users. NanoClaw's
adapter and deployment remain separate integration work.

`authorize(method, params)` is mandatory trusted host code. It authenticates the
connection and checks the source's current authority over that exact request,
task or host operation before every call, including reads and report retrieval.
Only literal `True` permits a call. Bind request and operation IDs to the same
authenticated source; an opaque ID alone is not authorization. A shared service
must enforce source isolation in this callback. Do not expose the owner object,
service object, callback, Python evaluator or bootstrap configuration to callers.

Each request has exactly these fields:

```json
{"schema_version":1,"id":"wire-correlation","method":"task_request","params":{"request_id":"host-job-17","task":"Requested outcome"}}
```

`schema_version` is integer 1, never a boolean. `id` is a nonempty string of at
most 128 characters. It correlates one reply and is not a durable operation ID.
`method` is from the table below; `params` is its exact object. Duplicate JSON
keys, unknown methods, extra envelope fields and extra method fields refuse.
The line must end in a newline and fit `MAX_REQUEST_CHARS` from the sidecar module.
JSON/report strings are untrusted task data, not controller instructions.

Success has exactly `schema_version: 1`, echoed `id`, `ok: true`, `result` and
`owner_receipt`. Failure has `schema_version: 1`, `id` (null if unparseable),
`ok: false`, and `error: {code, reason, action}`. Only stable reason codes and
safe recovery guidance cross this boundary; internal exception text is not
returned. A refusal emits a diagnostic to stderr and ends the connection with
return code 1. Clean EOF returns 0. Unexpected exceptions and pipe failures
propagate to the bootstrap as failures. They never replay a request.

## Caller Methods and Identity Mapping

All IDs below are nonempty strings of at most 128 characters. Task text retains
the original 65536-character bound. The original request and query params and
their `{task_id, status}` results remain unchanged.

| Method | Exact params | Result |
| --- | --- | --- |
| `task_request` | `{request_id, task}` | `{task_id, status}`; persist admission once, conflict on changed task bytes |
| `task_query` | `{task_id}` | `{task_id, status}` from the existing policy task ledger |
| `task_inspect` | `{task_id}` | `task_id`, `status`, `terminal`, `closure`, `reports`, `controls`, `effect_reconciliation_required` |
| `task_report` | `{task_id, report_id}` | `task_id`, `report` metadata and verified UTF-8 `content` |
| `task_cancel` | `{task_id, operation_id}` | durable host control record; immediate queued cancellation or pending foreman cleanup |
| `task_resume` | `{task_id, operation_id}` | durable pending host control record, admitted only while policy-blocked |
| `task_reconcile` | `{request_id}` | `{found, task_id, status}`; absent request returns false and nulls, without admission |
| `operation_query` | `{operation_id}` | `{found, operation}`; absent host control returns false and null, without mutation |

The host's job ID stays in the host database. The host binds it to a stable
`request_id` before sending start admission, then records the returned policy
`task_id`. A host cancel/resume attempt uses a separate stable `operation_id`.
That ID is immutable across task and action; changed reuse conflicts. The
wire `id` may change on reconnect. Internal Herdr effect IDs live in a separate
namespace and are never accepted as authority to reconcile an effect.

If an admission reply is lost, call `task_reconcile` with the original
`request_id`. If a cancel/resume reply is lost, call `operation_query` with the
original host `operation_id`. Retrying the identical original mutation is also
idempotent. Never invent a new request ID to recover uncertain work. A lookup's
`found: false` means only that this authenticated owner has no such record;
it says nothing about worker execution. Missing/corrupt owner history refuses,
never returning an empty success. Host lookups cannot clear internal pending
effects, even after restart.

## Status, Reports and Receipts

`status` remains the policy owner's vocabulary: queued, cancelled, closed,
within_authorized_budget, checkpoint_required, awaiting_diagnosis,
diagnosed_stop or dispatch_outcome_unknown. Admission and available correction
budget are not evidence that a worker started. Queries report durable policy
state, not a fresh native worker health probe. Only cancelled/closed are
terminal; `closure.details.outcome` distinguishes merged from abandoned.

Inspect returns the policy closure event or null, task-scoped assessed report
metadata, and host controls. Each report metadata row has schema 1, `report_id`,
`evidence: {path, sha256}`, `verdict` and `acceptance`. `report_id` is an opaque
owner-issued digest of the assessment identity; hosts use the returned ID.
These are the existing
assessment's facts, not a new acceptance decision. `task_report` takes that
report ID, never a caller path, verifies the assessed SHA-256, decodes UTF-8 and
refuses unavailable, changed or larger-than-1-MiB reports. The host treats report
content as untrusted data. Inspect's `effect_reconciliation_required` describes
the shared owner's effect fence; it does not attribute that effect to this job.

Each success includes an owner-issued schema-1 receipt with `attestation`
containing every original controller/principal, saved-machine/session,
workspace/pane/terminal, lease epoch and loaded-policy version/digest field.
The owner checks its current live lease, original binding and policy bytes
before issuing it. This is authenticated connection evidence, not a signed
bearer capability, live worker-health proof or model-tier proof. It includes
`production_enabled: false` and `reattachment_supported: false`.

## Policy Foreman Control Inbox

The trusted controller schedules the sole policy foreman when admission or a
control arrives and after restart. Before further dispatch on a task, the
foreman drains `pending_task_controls()`. This trusted inbox and
`resolve_task_control(operation_id, runner=...)` are never transport methods.
The host must not report a pending control as completed or enable a service
without the policy foreman consuming this inbox.

Cancellation before `claim_task` atomically marks the request cancelled and
completes the control without worker input. Queue scans omit it and later
claims refuse. The same owner lock serializes claim and cancellation. A claim
already persisted makes cancellation a post-handoff request, even if the
controller crashed before registration; restore that original registration.

Cancellation after handoff is asynchronous. It requests stopping this task,
not permission to issue caller-specified keys or close a pane. The foreman
uses its existing dispatch reconciliation, owned cleanup, supervision and
task closure owners. Resolution requires no pending remote effect, no uncertain
task dispatch, no active task supervision member, and the original task's
`abandoned` closure. The owner rechecks the live remote anchor and lease before
recording completion. Until these gates pass, the control remains pending.
Cancellation is not an undo of an already merged or published artifact.

Resume is admitted only for checkpoint_required, awaiting_diagnosis or
diagnosed_stop. It refuses queued, running-within-budget, terminal and uncertain
dispatch states. Admission never clears a diagnosis, grants new budget or
changes the task/base/history. The foreman follows the existing recovery and
authorization workflow, then resolves only when the original task reports
within_authorized_budget and the shared remote effect fence is clear. This
marks readiness to continue, not proof that a subsequent worker started.
Only one control may remain pending per task. Replaying a completed operation
returns its historical evidence without applying another control.

## Owner Records

`RemoteForemanOwner` alone writes/migrates the schema-2 owner document described
in `skills/herdr-foreman/references/remote-context.md`. The sidecar never edits
its files. This remains a gate store under `rules/stateful-artifacts.md`:
missing prior ownership, corruption and unsupported versions refuse with
recovery guidance, preserving all bytes. Readers must understand document 2,
request 3 and control 1 before serving this boundary.

Each control has exactly `schema_version: 1`, `operation_id`, `task_id`,
`action: cancel|resume`, `status: pending|completed`, and `evidence`.
Pending evidence is null. Completed evidence has exactly `schema_version: 1`,
`outcome: cancelled_before_dispatch|cancelled|resumed`, and `ledger_digest`
(SHA-256 lowercase hex). Before-dispatch cancellation hashes the cancelled
request row. Other completions hash the observed policy task state. Hash input
is UTF-8 JSON with sorted keys and compact separators. Digests bind historical
evidence; they never replace live checks or authorize subsequent effects.

Operation IDs are unique. Task IDs refer to the same document's requests.
A cancelled request has exactly one matching completed before-dispatch control
and digest. All other controls belong to registered requests; at most one is
pending for a task. Unknown control/evidence versions and mismatched records
refuse unchanged. No new sidecar state file or caller-written migration exists.

## Restart, Handoff and Enablement

A restart with the same authenticated lease, policy bytes and original store
preserves queued requests, claimed handoffs, controls, reports and uncertain
effects. Reconcile host response loss through the lookup methods; the foreman
reconciles effect uncertainty separately through its privileged owner API.

Lease/principal/controller rotation and policy upgrades still have no supported
history-preserving reattachment transaction. Changed receipts or policy bytes
refuse every caller operation without rewriting history. Keep production
deployment disabled; do not erase state, rotate a receipt in place or report
this service as a rolling-upgrade solution. NanoClaw still owns its adapter,
authenticated runtime/model-tier proof, bounded real Herdr compatibility test,
report delivery and end-to-end integration acceptance. This contract does not
enable its production adapter or authorize a deployment.
