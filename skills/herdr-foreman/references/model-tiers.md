# Model Tiers

## Configuration and supported workers

`config.example.json` is the operator-owned tier table. Config schema 7 replaces
the live `agents` roster with `worker_kinds`: launch/UI templates that exist
without a pane until a plan assigns them. Schemas 1–6 remain readable for
legacy standing-worker recovery. Schema 8 adds optional minimum-adequate worker
routing below; schema 7 remains the unchanged default in the shipped example.
The utility never rewrites the operator's config. Copy the example into a new
file, preserve local worker-kind names and UI options, then validate it with
`foreman.sh plan` before replacing a working configuration.

Each `tiers` entry maps a round type to:

```json
{
  "model": "claude-opus-5-5",
  "effort": "high",
  "multiplier": 1.0,
  "billing_evidence": null
}
```

Omit `effort` for a model that accepts no effort control. Omit
`billing_evidence` until measurements exist. The example carries no invented
live measurement.

Accepted round names, role mappings, model pins, effort enums, mechanical
eligibility, risk escalation, and supported launch options are the constants
and functions in `skills/herdr-foreman/foreman/tiers.py`. The parser rejects
unknown rounds and unsupported adapters. Claude Code, Codex CLI, and Grok
Build have launch adapters verified against their installed CLI help.
Antigravity's research column remains a future adapter; the example does not
declare an inactive agent or accept unused tier rows for it.

## Minimum Adequate Routing

Operator opt-in: set config `schema_version` to 8 and add `tier_routing` to a
worker kind. Keep its existing rows and account/window identity. Missing
`tier_routing` preserves configured-row behavior; `mode: pinned` is an
explicit operator override, recorded as such. The pinned judge and judgment
floor never opt down. Evidenced risk escalation is not lowered by this route.

`tier_routing` contains `mode` (`minimum_adequate` or `pinned`) and `evidence`,
an object keyed by configured tier row. Each evidence value carries:

| Field | Evidence required |
| ----- | ----------------- |
| `model`, `effort` | The observed pair; changing a row never rebinds old proof |
| `launch` | `status`: supported/unsupported/unknown; `ref`: provider catalog **and installed CLI launch support**; `checked_at`: actual timezone-bearing observation |
| `access` | `status`: accessible/unavailable/unknown; `ref`: actual launch/API result on this logged-in account; `checked_at`: actual observation; `window_group`: this configured account/window identity |

Use non-secret references to the observations, never login tokens. A configured
model ID, provider listing, quota percentage or another account's success
does not prove account access. Missing, stale, future or mismatched proof is
recorded and skipped, not fabricated. Config is operator-owned; the utility
validates and reads it, never writes or migrates it.

For a verified same-family version replacement, use
`skills/herdr-foreman/references/successor-placement.md` before changing config. Provisional
placement preserves the predecessor's authorized spot and qualification,
including unknown, without another per-role validation campaign. Fresh CLI,
account access and account-bound capacity remain independent requirements.
That reference also governs explicit null effort for a new no-effort successor.

The existing capability table supplies dated qualification for the **requested
round**, not the row's usual round. A build may borrow a mechanical row's pair
only when it is qualified for implementation; the task never becomes
mechanical and receives no oracle or authority waiver. Existing contribution
and worker-capability exclusions still run before tier candidacy.

The bound measurement supplies current remaining capacity and its account
identity independently of access proof. Selection chooses the minimum eligible
pair by declared resource weight, then adequate effort, with stable tie breaks.
These weights are proxies, not prices or monetary savings; unknown billing
attribution retains its conservative weight. Facts and freshness limits are
owned by `skills/herdr-foreman/foreman/tier_routing.py`. Apply rereads the bound
measurement, current config and qualification before native actions; stale
capacity or changed selection refuses and asks for replanning.

The existing selection record includes every candidate's four evidence classes,
rejection reasons and override. Refresh only stale/unknown facts relevant to
this task's candidates. Unrelated catalog or table maintenance remains a
separate maintenance task, never a delivery prerequisite. Existing sourced
benchmarks, evaluations and project results suffice; no new per-model role
validation programme is required.

The issue's requirement that reviewer/tester judgment stays at the top tier
governs scoped rechecks too. This resolves the research table's conflicting
lower-tier recheck examples. The pinned judge names one declared worker kind
and overrides its model and effort for that seat. It shares that kind's
configured usage window.

Schema-7 measurement starts one disposable probe at the `coordination` tier
for each distinct `window_group` in a separate unfocused workspace, reads
usage once, and closes the probe's root pane. It never types a usage command
into an idle assignment pane. Planning ranks the
stable worker-kind names, then records a fresh Herdr identity for every seat in
`assignments` and the selected templates in `worker_kinds`. Apply creates a
separate unfocused workspace for each identity, starts it in the returned root
pane at its selected tier, proves its launch argv, and
only then dispatches the brief. The identity remains stable for recovery of
that dispatch; it is never a reusable roster entry.

Every team worker starts in YOLO mode, including the pinned judge and release
worker. The foreman's assignment classifier checks each brief against the task's
authorization and permitted actions before dispatch. Worker permission prompts
are not a second assignment gate; the brief's role, path, and authority limits
still apply in YOLO mode.

`launch_args` preserves supported UI options across restarts. The launcher
supplies each runtime's YOLO flags and refuses conflicting permission options
before stopping a worker. The permission contract is in
`skills/herdr-foreman/foreman/tiers.py` (`YOLO_FLAGS`, `worker_launch_args`,
and `verify_worker_permissions`). Tier flags, resume options, command
strings, and prompt operands remain forbidden in `launch_args`; change the tier
table to change the model or effort.

Legacy standing workers started manually through Herdr pass the runtime's YOLO
options after `--`. Verify the resulting launch or foreground-process argv
before sending a brief. Existing legacy workers, including non-tiered workers,
require that same proof.
A non-tiered existing process also proves permission in the documented resume
form: the runtime's resume option or subcommand naming one explicit session
UUID as a separate token, plus its explicit YOLO flags. Which selectors that
form accepts and which it refuses is the grammar in the same module —
`RESUME_OPTIONS`, `RESUME_SUBCOMMANDS`, `RESUME_REFUSALS` and `SESSION_UUID`,
under the comment block above them. Tiered proof (`verify_argv`) stays exact
and accepts no resume form.
Do not clear a retained worker to repair a permission mismatch. Use the normal
fresh-dispatch boundary once the current assignment is resolved, or the
same-session restoration in `references/dispatch-recovery.md` when the
operator expressly requires YOLO for that retained developer.

Recheck model availability and CLI flag spellings when upgrading a worker's
CLI or changing a model pin.

## Launch Failure Maintenance

A classified owner output routes recovery without discarding the agent or
its provider. Read the emitted receipt; never classify provider prose yourself.

| Owner output | Class | Next step |
| --- | --- | --- |
| Wait exit 6 or `failure_kind: model_identifier_unavailable` | identifier maintenance | The repair below, after the dispatch lifecycle in Wait outcomes (Exit 6) |
| `failure_kind: launch_transient` | pre-input service failure | Follow the emitted retry or exhaustion outcome under dispatch recovery; no model retirement |
| `failure_kind: startup_dialog_pending` | retained native dialog | Runtime Dialogs and the named owner reconciliation |
| Recovery `outcome: blocked`, or dispatch `sending` / `sent_but_not_started` | unproved transport or cleanup | Preserve evidence and complete the named owner reconciliation before retrying |
| Wait exit 5 with `reason: terminal_provider_refusal` | provider refusal | `record-refusal` and its bounded provider-move contract |

Repair for `model_identifier_unavailable`:

1. Preserve the task, original base, correction count, artifacts and the
   actual dispatch effect. Reconcile an uncertain send before anything else.
2. Check three sources: the provider's current catalog, the installed CLI's
   model and flag spellings, and this account's actual access. Another
   account's success and a catalog listing each establish nothing about this
   account.
3. Repair the exact stale row in the operator's config with the identifier the
   sources support, or a verified same-family successor
   (`skills/herdr-foreman/references/successor-placement.md`). The utility never rewrites the
   config.
4. Validate with `plan`. A judgment row stays on the pinned top model at the
   effort floor; `TOP_MODELS` in `skills/herdr-foreman/foreman/tiers.py` names
   the accepted ids.
5. Dispatch the affected seat again. The provider, every other callable row
   and every `capabilities` entry stay as they were.

Catalog identity, account access and capability adequacy are separate facts.
A smoke call proves access; it never writes an `adequate` capability entry.
The independently pinned judge keeps its own pin and launch proof.

Detection: `skills/herdr-foreman/wait-report.sh` exit 6 after a send,
`identifier_unavailable_model` in `skills/herdr-foreman/foreman/composer.py`
at fresh startup, and the structured `agent_start` error codes
`IDENTIFIER_ERROR_CODES` / `TRANSIENT_LAUNCH_ERROR_CODES` in
`skills/herdr-foreman/foreman/errors.py`, classified at the spawn boundary in
`skills/herdr-foreman/foreman/lifecycle.py`. Only Herdr's own error code
classifies native-start errors; arbitrary provider prose never does.

## Foreman Seat

The foreman is a seat like every other. The operator's tier table supplies
its rows, as it does for every worker; no rule, plugin default or hardcoded
value pins its model or effort. The top-level `foreman` block (config schema 6)
declares it the way a worker is declared:

```json
{
  "agent": "foreman",
  "kind": "claude",
  "window_group": "claude-max-weekly",
  "launch_args": ["--dangerously-skip-permissions"],
  "tiers": {"coordination": {"model": "<model>", "effort": "<effort>"}}
}
```

`agent` is the Herdr name the foreman pane runs under; it is never also a
configured worker or the pinned judge. `tiers` is optional: without it the
seat reads the tier table of the first configured worker of its kind. A
`model` or `effort` field on the block is refused; the parser is
`parse_foreman` in `skills/herdr-foreman/foreman/config.py`. `launch_args` are
the operator's permission and UI options under a worker's grammar; the worker
YOLO requirement does not apply to this seat.

Tier selection for the seat takes three inputs: the seat's tier table, the
capability table, and the measured headroom of the seat's `window_group`; the
foreman's own pane is never usage-probed. The tier is the operator's
`coordination` row: no escalation applies to the coordination round, so
headroom never changes it and is only recorded. Selection returns that row
with its capability verdict and recorded headroom, and refuses when the table
has no `coordination` row or the capability table records the row inadequate. The selection is `_select_foreman_tier`
and `_foreman_headroom` in `skills/herdr-foreman/foreman/cli.py`, resolving
through `select_tier` in `skills/herdr-foreman/foreman/tiers.py`. The planner
never seats the foreman on a worker: `plan --roles foreman` is refused.

Start the foreman from any shell, naming an empty Herdr shell pane. This is a
synopsis; the runnable command, through `bash` and the resolved plugin root,
is `skills/herdr-foreman/SKILL.md` Step 2:

```text
foreman start-foreman --pane <pane-id>
```

It selects the tier, starts the configured agent with exactly `launch_args`
plus that tier's model and effort flags, and prints the selection and the
launch-argv proof. A launch argv that differs refuses. A pane whose foreground
holds anything but its shell refuses before anything starts. A retry is
idempotent: when Herdr already holds the seat's name in that pane with the
seat's kind, nothing starts, the live foreground argv must carry the selected
tier, and the result carries `replayed: true`. A name held in another pane or
by another kind, or a live tier other than the selected one, refuses
(`start_foreman` in `skills/herdr-foreman/foreman/launch.py`).

`foreman verify-foreman` re-runs the selection and proves the running foreman
from its pane's live foreground argv, this Herdr pane by default or
`--pane <pane-id>`. The pane must be the one Herdr binds to the seat's agent
name, with the seat's kind; any other pane refuses, whatever tier it runs. It
reads state without the lock and never persists a migration: an older state
file refuses with the owner command to run. Step 2's round preflight runs it inside one composite check
with `foreman measure`, `skills/herdr-foreman/foreman-tier-check.py`, and records
its `checks.headroom` and `checks.foreman_tier` rows:

- the result carries the pane's process-argv proof of the selected model and
  effort — `ok`
- it carries another tier or no such proof, the pane is not the seat's, or the
  selection refuses — `failed`, and the round
  blocks until the operator restarts the foreman with `start-foreman`
- config has no `foreman` block — `unconfigured`, a stderr warning; its
  `detail.warning` names the resolved config file, the `schema_version` 6 it
  needs and the minimal `foreman` block to add, then `start-foreman` to launch
  it; the round proceeds
- `checks.headroom` did not pass — the composite runs only
  `verify-foreman --config-only`, which reads config presence without
  selecting or probing; a configured foreman records `failed` as a dependency
  on headroom, and an absent block is still `unconfigured`

The composite owns the verdict and exits with it: 0 ready, 1 not ready with
each blocking reason in its rows, 2 could not run (the exit contract is in the
script's docstring). The preflight records the rows on 0 or 1. Any other exit,
unreadable output, or rows that contradict the exit code fail both rows.

A selection that moves, on a capability-table verdict or a table edit, reads as
a mismatch at the next preflight; the restart picks the new tier. A context
reset keeps the pane's process, so the proven tier survives it.

## Planning and dispatch

`plan --round ROLE=ROUND` selects a configured round type. Without it, the
role's default applies: each role's default is the cheapest round its contract
allows (`DEFAULT_ROUNDS` in `skills/herdr-foreman/foreman/tiers.py`). A
consultation that must settle something moves to its judgment round on the
evidence in its round context, never on a remembered override. Which evidence
moves which role is `ESCALATION_EVIDENCE` in the same file; the steps that
consult an investigator or staff a fired trigger name the field to pass. With
that evidence, a `consultation` round request is refused. Pass
`--round release=release_adjudication` when the release worker must interpret
a dispute; an ordinary dispute still goes to the judge.

A `consultation` or `test_plan` row may name a non-top model. The example's
Codex `consultation` and `test_plan` rows at `medium` are a starting point, not
a measured result; record a measurement in the capability table before
relying on it. Config schema 4 refuses a tier table without a `consultation`
row; copy the example's row, never synthesize one from `build`. A schema-3
table keeps the judgment defaults it was written against (`state-schema.md`).

`--round-context FILE` reads a JSON object keyed by assigned role. A
mechanical context has this shape:

```json
{
  "developer": {
    "oracle": {"kind": "patch", "path": "/abs/path/to/exact.patch"}
  }
}
```

A developer's `mechanical` round is licensed by one thing: a whole-result oracle,
the expected result written down for a later comparison. `kind` is `digest`,
`patch` or `fixture` — an expected sha256 in `value`, or a file in `path`
holding the patch or the complete expected output.
The declaration is checked, not taken: a digest of the wrong shape and a file
nobody wrote both refuse the round.

Which shapes qualify is the decision contract of
`skills/herdr-foreman/foreman/tiers.py`, not the foreman's — see
`mechanical_allowed`, not restated here (`rules/script-as-black-box.md`).

The licence holds only if the comparison runs. Before a mechanical round is
accepted, `verify-oracle` compares its whole result against the oracle the plan
declared (SKILL.md Step 12). The result is the pushed diff for a `patch` oracle
and the produced output otherwise. `plan` pins each `patch` or `fixture`
file's sha256 in the plan's `oracle_pins`; an oracle file edited after planning
is refused rather than compared. `apply` binds the oracle and its pin into the
round's dispatch, and `verify-oracle --task` checks against that binding, so a
plan edited after dispatch is refused too. The comparison is the contract of
`skills/herdr-foreman/foreman/oracle.py`, `verify`.
A context written for the retired predicate — task names, `spec_complete`,
file and byte caps, the escape booleans — is refused by name, with its
replacement, rather than silently ignored. Other context fields cover failed
gates, prior High misses and risk flags. Supply `--fix-round` to
both plan and apply for a fix; a plan made for a different fix context is
refused. A new tiered role requires an explicit cost instead of inheriting an
unrelated fallback weight.

Plan schema 4 carries `tiers`, `rounds`, and the task's `task_context` alongside `assignments`. Apply
records the requested task phase as `round` and the selected config row as
`tier_row`; escalation may select a stronger row and raise its effort. Apply
recomputes the tier from current config and round inputs, refusing a stale or
edited pair. `apply --dry-run` prints the requested tier and relaunch argv
without contacting Herdr or writing state. It does not establish live
readiness, process identity, or session continuity.

The owner validates correction allowance before selecting tiers. A bounded
approval does not lower a late correction's tier or reset its cumulative
number. A fresh developer handoff after a required release clear uses the
normal relaunch checks; verified retained fixes keep their
existing compatible model and effort.

Schema-7 fresh dispatch creates a separate workspace without changing focus
and calls `herdr agent start` in its returned root pane under the plan-bound
identity with the selected flags. The returned
worker identity and argv must match before the brief is sent. A failed spawn
closes only its created root pane; after any prompt may have landed, recovery
preserves
the pane and recorded identity. Legacy tiered dispatch retains the prior
idle/composer/process verification and relaunch path. No command is sent to a
working or blocked worker.

### Maintenance Relaunch

`foreman relaunch-worker <name>` is the legacy standing-worker owner route for
the same verified relaunch outside a dispatch, including recovery from a pending CLI update that
blocks measurement. It accepts no tier override: the old process argv must
prove exactly one model/effort pair in that worker's current tier table. Before
termination it requires the worker to be idle, its visible composer to be
empty, and retrospective coverage for the outgoing assignment. It then starts
the same configured pair with normalized `launch_args` and YOLO mode, reads
the new foreground process, and returns `verified.source: process_argv` with
`dispatch: null`. It appends a non-counting `maintenance` assignment-ledger row
with the model, effort, configured launch arguments, verified pair, and live
process-argv evidence source, but creates no dispatch. A working or blocked worker, an occupied composer, an
unconfigured live pair, or a changed pane, PID, or argv refuses before termination.

`--no-clear` and `--retain-context` verify the running foreground process
arguments, including YOLO mode, instead of restarting it. If the process record lacks argv, the
transport reads `ps` for that same foreground PID. Older Herdr builds that
cannot supply the structured process record fail closed. A retained fix also
needs the existing task/fix history and live native session identity; it keeps
a compatible higher effort instead of restarting to lower effort. Its recorded
`de_escalated` describes the tier it runs at: a kept effort that reaches the
step the plan declined clears it, and a declined model switch keeps it set.

Tiered assignment messages include the selected model, effort, and an input
`prompt_hash`. The hash covers length-framed bytes of the original assignment
message, COMMON.md, and the role brief, excluding the generated metadata
footer. Workers report observed CLI version, tokens, compactions, and quota
windows; unavailable observations remain unknown. These reports are
measurements, not substitutes for launch proof or the paired battery.

The executable launch and verification contracts live in
`skills/herdr-foreman/foreman/launch.py` and the argv builders in
`skills/herdr-foreman/foreman/herdr.py`. Herdr's installed 0.8.2 schema and
the [socket API](https://herdr.dev/docs/socket-api/) describe the start and
foreground-process response shapes. Banner or transcript text is never a
verification source.

## Capability table

What each model at each effort can do, sourced and dated. Published knowledge
the project imports, not a measurement it takes: routing reads it to pick a
model, and a capability nobody has evidence for is recorded `unknown` rather
than assumed.

Saved at `<selected-state>.capabilities.json`. Schema 2 adds provisional
placements under `skills/herdr-foreman/references/successor-placement.md`; entry schema remains 1.
Schema 1 below is the prior envelope. The owner upgrades it on read through
`capability-migrate` or either record command; entries and refresh time remain
untouched. Read-only readers refuse it until that
upgrade and emit the owner-command diagnostic. Owner:
`skills/herdr-foreman/foreman/capabilities.py`. Writer: `capability-record`,
and `capability-successor` for placements. Readers: `capability-check`, `capability-show`, the round preflight,
`plan`, `apply` and `start-judge`, which refuses a pinned judge the table
records inadequate before anything launches. `plan` and `apply` read it read-only through
`capabilities.load`: a missing file is an empty table, a table written by a
newer build refuses with an update diagnostic and is left untouched, and an
unreadable or malformed one refuses the command naming the file. A symlink at
the table's path, live or dangling, refuses the command and is left as found.

```json
{
  "schema_version": 1,
  "refreshed_at": "2026-09-01T00:00:00+00:00",
  "entries": [
    {
      "schema_version": 1,
      "model": "opus-5", "effort": "high",
      "capability": "independent-defect-detection",
      "verdict": "adequate",
      "source": {"kind": "benchmark", "ref": "SWE-bench Verified", "dated": "2026-09-01"},
      "recorded_at": "2026-09-01T00:00:00+00:00"
    }
  ]
}
```

| Field | Presence | Meaning |
| ----- | -------- | ------- |
| `schema_version` | required | The document's version; each entry carries its own |
| `refreshed_at` | required, UTC or `null` | The last `capability-record`; `null` until the first one |
| `entries` | required array | One entry per model, effort and capability, sorted by those three |
| `entries[].model`, `effort`, `capability` | required | The key; no two entries share one |
| `entries[].verdict` | required | `adequate`, `inadequate` or `unknown` |
| `entries[].source` | required | `kind`, `ref` (where it was read) and `dated` (`YYYY-MM-DD`, when it was read) |
| `entries[].recorded_at` | required, UTC | Stamped by the writer, never supplied by the report |

An absent file reads as an empty table: `refreshed_at: null`, no entries.
This artifact uses the `rules/stateful-artifacts.md` gate-store exception:
an inadequate entry is an open gate refusing a launch. Readers refuse any
schema they do not accept, without discarding negative evidence. A newer
record names the plugin update; schema 1 names the owner migration, which
rewrites it before use without changing evidence. Writers never overwrite
a newer record. An unsupported older or malformed file is refused with its repair, and so is a
symlink in the file's place. No field has a
default: an entry missing one is refused, never filled in.

`capability-record --record <report.json>` reads the consultation's report,
shaped `{"entries": [...]}`: each entry carries `model`, `effort`,
`capability`, `verdict` and `source`, and nothing else. The writer stamps
`schema_version` and `recorded_at`, replaces the entries whose key the report
covers, keeps every other entry, and sets `refreshed_at`. A report with no
entries or recalibrations refreshes nothing and is refused. Recalibrations use
`skills/herdr-foreman/references/successor-placement.md`; a recalibration-only report leaves the
table's `refreshed_at` unchanged. A result this project recorded is a
`project` source and cites its issue.

Which source kinds exist, and which of them can support an `adequate` verdict,
are `SOURCE_KINDS` and `SUPPORTING_SOURCES` in
`skills/herdr-foreman/foreman/capabilities.py`, not restated here
(`rules/script-as-black-box.md`).

Staleness is silent — a retired entry keeps routing work with no error and no
failing check — so the table comes due on a cadence. The round preflight
reports it under `due`, and SKILL.md Step 2 carries the refresh commands:
`capability-check` (read-only, whether a refresh is due), `capability-record`
and `capability-show`. A table never refreshed comes due as
soon as the ledger holds any work, and a fleet that dispatched nothing never
comes due. The interval and the source hierarchy are `skills/herdr-foreman/foreman/capabilities.py`'s
decision contract, not restated here (`rules/script-as-black-box.md`).

The consultation that gathers the evidence is read-only on repository content
and returns a report at the path its brief names; the foreman records it. A refresh
replaces the rows it covers and leaves every other row untouched, so one report
about two models never retires the rest of the table.

## Billing-window evidence

Collect billing evidence before assigning a tier a separate pool. Run an
isolated mechanical round with its requested model and effort, recording all
visible quota windows before and after. Capture the CLI version, exact prompt
hash, and observation timestamp. For Spark, include the main weekly window
as well as Spark's window; for Sonnet, include Claude's shared weekly window.
Concurrent users of the subscription invalidate the isolation claim.

The `billing_evidence` object has this contract:

```json
{
  "schema_version": 1,
  "isolated": true,
  "model": "<exact model id>",
  "effort": "<effort or null>",
  "cli_version": "<observed CLI version>",
  "prompt_hash": "<SHA-256 of the executed prompt>",
  "measured_at": "<observation timestamp>",
  "before": {"<window name>": {"remaining_pct": 80, "reset_at": "<reset timestamp>"}},
  "after": {"<same window name>": {"remaining_pct": 79, "reset_at": "<same reset timestamp>"}}
}
```

Include every observed window in both maps, including unchanged ones. The
attribution predicate is in `skills/herdr-foreman/foreman/billing.py`.
Incomplete, reset, rounded-away, ambiguous, or differently bound evidence
produces `unknown`. `measure` records each configured tier's model, effort,
and window under `agents.<name>.tier_billing`, including skipped and failed
measurements. Unknown attribution earns no speculative cost reduction.
Declared shared-window membership remains in effect.

No live isolated billing result was available during implementation outside a
Herdr team session. The shipped example therefore uses unknown attribution;
its deterministic tests are synthetic evidence of behavior, not observations
about provider billing. Do not copy test evidence into a live configuration.

## Selection records and cost through acceptance

Every plan records, per assignment, why it got its model and effort: the
required capabilities, the selected pair, the capability-table evidence, each
cheaper candidate with its verdict or an unknown cost, and the escalation
conditions. Opted-in routing also records separate launch support, account
access, qualification and capacity, candidate refusals and overrides. The
record shape is plan schemas 17/18 in `state-schema.md`; it
explains a selection and never changes one.

`cost-report` reports each task's resource use through acceptance from the
state file alone, as JSON, each quantity separately. Token counts are not in
any owner record and read `unknown`, listed under `unrecorded`. A shared or
concurrently used window keeps `attribution: unknown`. The report makes no
savings claim: a tier's quota or multiplier change is never read as a cost
reduction. The output contract is in `state-schema.md` (Writer / Reader
Contract); what each field counts is `skills/herdr-foreman/foreman/cost_report.py`.

## What stands in for a validation battery

No per-model, per-effort, per-role battery gates a tier. Three things already
cover what one would catch:

1. Judgment rounds run on the pinned top model; `parse_tiers` in
   `skills/herdr-foreman/foreman/tiers.py` refuses a lower one.
2. Every other round's output passes independent review and testing before
   release.
3. Which model suits which job is the capability table above: sourced, dated
   rows, refreshed on a cadence. Tier selection reads it for every candidate:
   an `inadequate` entry for the selected model and effort refuses that
   candidate, naming its source. Legacy configured routing keeps its row on
   `unknown`; minimum-adequate routing skips an unknown or stale candidate
   unless its exact authorized spot has provisional successor placement under
   `skills/herdr-foreman/references/successor-placement.md`. Qualification itself stays unchanged.
   Which capabilities each round needs, and the only names
   `capability-record` accepts, are `ROUND_CAPABILITIES` and `VOCABULARY` in
   `skills/herdr-foreman/foreman/capabilities.py`

The pinned judge start path keeps its own launch proof. The operator owns
billing inputs inside config.json; these readers never write or migrate that
file. The state owner records launch proof in assignment rows; see
`state-schema.md` for reader behavior.
