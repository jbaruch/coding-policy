# Model Tiers

## Configuration and supported workers

`config.example.json` is the operator-owned tier table. Config schema 2 adds
per-agent `tiers` and `launch_args`; schema 1 remains readable without tiers.
The utility never rewrites the operator's config. Copy the example into a new
file, preserve local agent names and UI options, then validate it with
`foreman.sh plan` before replacing a working configuration.

Each `tiers` entry maps a round type to:

```json
{
  "model": "opus-5",
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

The issue's requirement that reviewer/tester judgment stays at the top tier
governs scoped rechecks too. This resolves the research table's conflicting
lower-tier recheck examples. The separate pinned judge remains outside the
rotating workers' tier tables and shares its configured usage window.

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

For initial manual starts through Herdr, pass the runtime's YOLO options after
`--`. Verify the resulting launch or foreground-process argv before sending a
brief. Existing workers, including non-tiered workers, require that same proof.
A non-tiered existing process also proves permission in the documented resume
form: the runtime's resume option or subcommand naming one explicit session
UUID as a separate token, plus its explicit YOLO flags. Which selectors that
form accepts and which it refuses is the grammar in the same module —
`RESUME_OPTIONS`, `RESUME_SUBCOMMANDS`, `RESUME_REFUSALS` and `SESSION_UUID`,
under the comment block above them. Tiered proof (`verify_argv`) stays exact
and accepts no resume form.
Do not clear a retained worker to repair a permission mismatch. Use the normal
fresh-dispatch boundary once the current assignment is resolved, or the
same-session restoration in `skills/herdr-foreman/references/dispatch-recovery.md` when the
operator expressly requires YOLO for that retained developer.

Recheck model availability and CLI flag spellings when upgrading a worker's
CLI or changing a model pin.

## Foreman Seat

The foreman is a seat like every other. The operator's tier table supplies
its rows, as it does for every worker; no rule, plugin default or hardcoded
value pins its model or effort. The top-level `foreman` block declares it the
way a worker is declared:

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

The foreman's round type is `coordination` (`COORDINATION_ROUND` in
`skills/herdr-foreman/foreman/tiers.py`), with its required capabilities in
`ROUND_CAPABILITIES` in `skills/herdr-foreman/foreman/capabilities.py`.
`select_tier` resolves the seat's `coordination` row with the measured
headroom of the seat's `window_group`, the usage window it shares with measured
workers; the foreman's own pane is never usage-probed. Under measured scarcity
the round declines a discretionary escalation, as any non-judgment round does
(`rules/agent-team-operation.md` Round Tiers), and never selects below the
configured row. The capability table then assesses the selected row. Selection
returns that row with its capability verdict and pressure headroom, and refuses
when the table has no `coordination` row or the capability table records that
row inadequate; a cheaper row never stands in for it. The selection is `_select_foreman_tier` and `_foreman_headroom` in
`skills/herdr-foreman/foreman/cli.py`. The planner never seats the foreman on a
worker: `plan --roles foreman` is refused.

Start the foreman from any shell, naming an empty Herdr shell pane. This is a
synopsis; the runnable command, through `bash` and the resolved plugin root,
is `skills/herdr-foreman/SKILL.md` Step 2:

```text
foreman start-foreman --pane <pane-id>
```

It selects the tier, starts the configured agent with exactly `launch_args`
plus that tier's model and effort flags, and prints the selection and the
launch-argv proof. A launch argv that differs refuses.

`foreman verify-foreman` re-runs the selection and proves the running foreman
from its pane's live foreground argv, this Herdr pane by default or
`--pane <pane-id>`. Step 2's round preflight runs it inside one composite check
with `foreman measure`, `skills/herdr-foreman/foreman-tier-check.py`, and records
its `checks.headroom` and `checks.foreman_tier` rows:

- the running argv carries the selected tier — `ok`
- it carries another tier, or the selection refuses — `failed`, and the round
  blocks until the operator restarts the foreman with `start-foreman`
- config has no `foreman` block — `unconfigured`, a stderr warning with the
  configure command in `detail.warning`; the round proceeds
- `checks.headroom` did not pass — the composite runs only
  `verify-foreman --config-only`, which reads config presence without
  selecting or probing; a configured foreman records `failed` as a dependency
  on headroom, and an absent block is still `unconfigured`

A selection that moves, on new capability evidence or a table edit, reads as
a mismatch at the next preflight; the restart picks the new tier. A context
reset keeps the pane's process, so the proven tier survives it.

What the foreman does on that tier, and where each judgment it used to make
now routes, is `rules/agent-team-operation.md` Foreman Seat and
`skills/herdr-foreman/SKILL.md`.

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
table keeps the judgment defaults it was written against (`skills/herdr-foreman/state-schema.md`).

`--round-context FILE` reads a JSON object keyed by assigned role. A
mechanical context has this shape:

```json
{
  "developer": {
    "oracle": {"kind": "patch", "path": "/abs/path/to/exact.patch"}
  }
}
```

A developer's `mechanical` round is licensed by one thing: a whole-result oracle, the
expected result written down where a later check compares against it byte for
byte. `kind` is `digest`, `patch` or `fixture` — an expected sha256 in `value`,
or a file in `path` holding the exact patch or the complete expected output.
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

Fresh tiered dispatch verifies the worker is idle in the expected pane and
its composer is empty, identifies its foreground process, terminates that
process, waits for the shell, and calls `herdr agent start` with the selected
flags. The returned worker identity and argv must match before the brief is
sent. A failure after termination may leave a shell or an unbriefed worker;
inspect the named pane before retrying. No command is sent to a working or
blocked worker.

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

Saved at `<selected-state>.capabilities.json`. Owner:
`skills/herdr-foreman/foreman/capabilities.py`. Writer: `capability-record`,
alone. Readers: `capability-check`, `capability-show`, the round preflight,
`plan`, `apply` and `start-judge`, which refuses a pinned judge the table
records inadequate before anything launches. `plan` and `apply` read it read-only through
`capabilities.load`: a missing file is an empty table, a table written by a
newer build is read as empty with a warning and left untouched, and an
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

An absent file reads as an empty table: `refreshed_at: null`, no entries. A
file stamped with a newer schema than the reader owns reads as no prior state,
with a diagnostic to update the plugin; `capability-record` refuses to write
over it. An older or malformed file is refused with its repair, and so is a
symlink in the file's place. No field has a
default: an entry missing one is refused, never filled in.

`capability-record --record <report.json>` reads the consultation's report,
shaped `{"entries": [...]}`: each entry carries `model`, `effort`,
`capability`, `verdict` and `source`, and nothing else. The writer stamps
`schema_version` and `recorded_at`, replaces the entries whose key the report
covers, keeps every other entry, and sets `refreshed_at`. A report with no
entries refreshes nothing and is refused. A result this project recorded is a
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
conditions. The record shape is plan schema 13 in `skills/herdr-foreman/state-schema.md`; it
explains a selection and never changes one.

`cost-report` reports each task's resource use through acceptance from the
state file alone, as JSON, each quantity separately. Token counts are not in
any owner record and read `unknown`, listed under `unrecorded`. A shared or
concurrently used window keeps `attribution: unknown`. The report makes no
savings claim: a tier's quota or multiplier change is never read as a cost
reduction. The output contract is in `skills/herdr-foreman/state-schema.md` (Writer / Reader
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
   candidate, naming its source, and `unknown` leaves the configured row in
   place. Which capabilities each round needs, and the only names
   `capability-record` accepts, are `ROUND_CAPABILITIES` and `VOCABULARY` in
   `skills/herdr-foreman/foreman/capabilities.py`

The pinned judge start path keeps its own launch proof. The operator owns
billing inputs inside config.json; these readers never write or migrate that
file. The state owner records launch proof in assignment rows; see
`skills/herdr-foreman/state-schema.md` for reader behavior.
