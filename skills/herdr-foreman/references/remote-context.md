# Attested Remote Foreman Context

This is a coding-policy-owned, single-controller Python integration surface.
It is not a public command executor, SSH adapter, second LLM foreman, deployment
service, multi-host lock or permission to merge. Native CLI and native hooks keep
their real `HERDR_ENV`, session and foreground-process checks.

## Controller Bootstrap

Only trusted service configuration constructs `HerdrCommandContext` and
`RemoteForemanOwner`. The service first loads the installed in-tree rules and
Herdr foreman skill/references in full. `loaded_policy(plugin_root)` computes
the version and digest of those bytes; the hash alone does not prove that a
model loaded or followed the policy. The controller attests that load.

The immutable receipt binds `controller_id`, `service_principal`, opaque saved
`machine_profile_id`, `remote_session`, `workspace_id`, `foreman_pane_id`,
`foreman_terminal_id`, positive `lease_epoch`, `policy_version` and
`policy_digest`. These are deployment configuration and controller authority,
never fields from an incoming task. The receipt grants no new task authority.

Use one actual saved machine profile, enabled and configured by the operator.
Its remote session is part of the profile. The only supported prefix is the
two-item tuple `("--machine", profile_id)`. No `--session`, `--remote`, shell
string, `eval`, whitespace splitting or fallback to the local server applies.

Create the owner store in an absolute, controller-owned mode-0700 directory.
The owner record must be a regular controller-owned mode-0600 file, not a
symlink. `initialize(runner, at=ISO)` verifies the live session and terminal
anchor before writing the receipt and a distinct remote supervision binding.
It does not replace an existing controller, principal, lease or pending intent.
Every competing controller of this deployment uses the same owner store and
OS lock. A separately copied store is not a lease and must not be enabled.

The workstation foreman seat is a **thin terminal anchor without an LLM**.
The controller running the loaded coding-policy foreman is the sole brain.
An agent detected in the anchor refuses preflight. Starting another agent in
it, typing a replacement foreman prompt or closing it through the worker owner
is forbidden. A worker may be split from the anchor; it is a separate pane.

`preflight(runner)` validates this context's live ownership and returns a
context-ready receipt, not a waiver of repository authority, staffing, model
tiers, reports, supervision or release gates. The trusted foreman passes the
bound client to the existing Python owners, including `cli.main(..., client=...)`.
It keeps using coding-policy's planner, tier selector, worker launch proof,
task/recovery ledger, report gates and release owner. No caller-provided model
or provider selection reaches them. Native workers retain their genuine Herdr
environment; the controller never manufactures one for itself.
The native `verify-foreman` command refuses this context: remote ownership is
not controller model-tier proof. A consuming service must supply that proof
through its authenticated controller runtime before enabling a team round.

## Caller Boundary

The service transport exposes only these two methods:

| Operation | Accepted request | Result |
| --- | --- | --- |
| Request task | `{"request_id": "stable-source-id", "task": "requested outcome"}` | `task_id`, `status` |
| Query task | `{"task_id": "existing-id"}` | `task_id`, `status` |

Every additional field is rejected. Requests cannot supply executables, argv,
SSH details, shell text, worker/provider/model choices, pane commands, provider
retirement decisions or an alternate foreman prompt. Task text is untrusted
task data, not replacement policy or permission to choose any of those. The
service authenticates the source and its existing task authority before ingress.
An idempotent request with changed task bytes is a conflict, not an update.

The policy foreman reads the queued requests, registers their returned
`task_id` in the existing task owner, and coordinates them under the loaded
policy. Queue acceptance is not a dispatch or a completed task. Transport code
does not plan, dispatch, adjudicate, correct or publish. Do not expose the
owner object, client, initialization, reconciliation or internal CLI to callers.

## Effect and Reconciliation Contract

Before each effect, the owner holds the live controller lock, rereads its
receipt and loaded policy bytes, checks the remote supervision binding, and
observes `status server` plus `api snapshot` through the same typed prefix.
The session selector, workspace, pane and terminal must still match. The
controller/principal/lease and policy load must still match the current record.
A stale epoch or changed identity refuses before the effect invocation.

The owner writes an indeterminate intent **before** invoking Herdr once. An
exit-0 response resolves transport delivery only, not semantic completion.
Timeout, disconnect, nonzero exit or an unexpected interruption preserves the
intent. No second start, prompt, text, key or close follows, including cleanup
and after controller restart. A provider failure inside an uncertain transport
result is not a provider-retirement signal. Native retry classification remains
unchanged outside this remote boundary.

Only the policy foreman calls `reconcile(operation_id, outcome, evidence,
runner)`. It observes the same identities again, evaluates actual task/worker
evidence, then records `applied` or `not_applied` and the evidence digest.
No response, missing agent, stopped process or elapsed timeout alone proves
`not_applied`. Identity drift prevents ordinary reconciliation and leaves the
intent open. Reconciliation never repeats the effect. Recovery uses the
existing task identity, original base, correction count and artifacts.

Raw `ps` and `kill` are not forwarded or run locally for remote worker PIDs.
Missing remote argv/process evidence refuses the affected operation. Native
context-reset hooks act on native foreground sessions only; they do not clear
the thin remote anchor. A controller requiring a reset must preserve its own
supervision obligations through its host runtime's planned continuation.

## Schema and Writer / Reader Contract

`RemoteForemanOwner` is the sole writer of the configured owner JSON. Service
transports use its request/query methods; they never edit or migrate it.

Document schema 1 has exactly:

- `schema_version`: integer 1.
- `attestation`: all receipt fields above.
- `state_path`: canonical absolute path of the existing foreman task state.
- `pending`: null or one schema-1 intent with `id`, `operation`,
  `status: "indeterminate"` and `argv_digest`. No raw prompt, argv or secret is
  saved in this intent.
- `reconciliations`: schema-1 rows with `operation_id`, `outcome` and
  `evidence_digest`.
- `requests`: schema-1 rows with `request_id`, untrusted `task`, `task_id` and
  `status: "queued"`. Task progress remains owned by the existing task ledger.

This is a gate store under `rules/stateful-artifacts.md`: missing owner records
require explicit first-use initialization. Corrupt, older and newer unknown
versions refuse and remain untouched; this first schema has no older supported
version to migrate. Never discard a pending record to unblock a deployment.
An existing supervision binding or discovery record proves prior ownership.
If the remote owner record is missing, initialization refuses before recreating
it; restore its original intents, reconciliation and request history first.

Remote supervision uses a distinct schema-2 **binding record** with `at`,
`state_path`, `generation` and identity `{kind: "attested-remote", cwd, pane_id,
attestation}`. Native binding records stay schema 1 with their unchanged
native identity. The containing supervision document and its other row types
remain schema 1. These are two current record types, not a native-to-remote
migration. Older native-only owners refuse schema-2 bindings; they must not
resume a remote deployment. Changing mode requires explicit reconciliation,
not automatic conversion of a native session or its obligations.

## Bounded Compatibility Test

Use an owned named Herdr server, a real saved machine profile and actual SSH
forwarding. Loopback SSH is valid for this bounded transport test. Do not reuse
production sessions, modify normal SSH configuration or fabricate CLI replies.

Load the source policy; issue the owner receipt for the observed thin anchor;
initialize and preflight it with no `HERDR_ENV` set for the controller. Use
the existing policy tier selector and launch owner to start one worker in a
separate pane, inspect its native session/process argv through the bound
client, send a bounded fixture prompt, observe the result, and close only that
owned worker. Confirm the anchor remains non-LLM and the caller rejects model,
provider and routing overrides. Stop/delete the owned server, remove its saved
profile and temporary SSH fixture. Do not substitute mocked transport for this
test or treat it as full NAS deployment acceptance.

Remote deployment remains disabled unless this command-surface compatibility
passes and the consuming trusted controller supplies its authenticated service,
shared live lease, policy load, task authority and evidence/report transport.
Coding-policy supplies the owner boundary; this feature does not provision a
NAS service or enable a consuming repository's integration.
