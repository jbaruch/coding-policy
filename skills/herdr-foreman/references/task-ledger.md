# Task Ledger

The foreman records its judgments in one persistent `TASK-LEDGER.md` for the task.
Its location, schema, ownership, and migration contract are in
`skills/herdr-foreman/state-schema.md`, Task Ledger. The JSON utility ledger
continues to record what was dispatched and which recovery decisions were
authorized. An `applied` row there says nothing about whether the work passed.

## Start or Resume

At Step 3, establish the stable task identity, original base, and absolute ledger
path. Save that path beside the task's authorization context and include it in
every foreman handoff. Create the document on a first run, or read the existing
document in full before planning more work. Do not create a new ledger per fix.

Read the referenced utility state through its owner commands. Correlate each
assignment with its dispatch ID, worker, role, and fresh report path. Never edit
`state.json` by hand to make it agree with a Markdown decision.

On resume, inspect evidence relevant to the next action: the actual report and
its content, branch tip/diff, review and test results, release state, and live
pane/native evidence for work that may still be running. Stale Herdr `working`
does not undo accepted work; `idle` or `done`, however often observed, does not
accept work. A conflicting observation requires reconciliation, not a resend.

## Record Decisions as They Happen

Append an event after each dispatch outcome, wait outcome, and evidence-based
assessment. Record missing reports and unknown send outcomes before continuing
another worker. Append blocker, judge, release, cleanup, and handoff decisions
when they happen; Step 16 finalizes the log rather than creating it from memory.

Pick each event's `decision` from its subject's vocabulary in
`DECISION_MEANINGS` at the top of `skills/herdr-foreman/foreman/members.py`,
which also gives each decision's meaning. Task and assignment decisions are
separate vocabularies.

Record Herdr's label under `observed`, with its source. Record worker claims as
claims there too. Neither is the foreman's `decision`. `found: true` confirms report
delivery only. A report containing `## BLOCKED` is not successful task completion.
A reviewer assignment can be accepted as a completed review while its blocking
findings keep the task `in_progress`.

Bind every acceptance to the specific report content and artifact inspected.
Record the full SHA for repository work and link test/review evidence for that
SHA. Preserve evidence files for resume. A new push makes older verification
insufficient for release: append that invalidation, put the task back in progress,
and obtain Step 12's current-tip verification. Keep the old report and decision.

Verify release claims against the live PR, merge, and any required publication
checks under `rules/ci-safety.md`. An accepted release report is not a substitute
for those checks. Record remaining obligations explicitly before claiming the
task complete. The ledger never bypasses dispatch readiness or permits input
into a working or blocked worker.

## Blank Document and Event

Fill this as an evidence log; placeholder values are not evidence. Every
statement about the task's outcome or quality quotes or names the report or gate
evidence that states it; the foreman's own prose covers process observations
only (`rules/agent-team-operation.md` Foreman Seat).
Append one uniquely named event section per decision. Each placeholder names
the `skills/herdr-foreman/foreman/members.py` constant that fixes its format;
write the constant's value, never its name.

```markdown
---
schema_version: <LEDGER_SCHEMA_VERSION>
task: <stable task identifier>
base_revision: <original commit, matching LEDGER_SHA>
dispatch_state: <absolute path of the utility state.json>
---

# Task Ledger

## <unique event id>

- schema_version: <LEDGER_SCHEMA_VERSION>
- id: <same unique event id>
- at: <observation time, timezone-qualified>
- subject: <one of SUBJECTS>
- dispatch_id: <actual dispatch id, or NOT_APPLICABLE per ASSIGNMENT_IDENTITY>
- worker: <worker name, or NOT_APPLICABLE per ASSIGNMENT_IDENTITY>
- role: <role, or NOT_APPLICABLE per ASSIGNMENT_IDENTITY>
- report: <absolute path, or one of REPORT_PLACEHOLDERS>
- observed: <source and its actual observation>
- decision: <one of DECISION_MEANINGS[subject]>
- head_revision: <inspected commit matching LEDGER_SHA, or one of HEAD_PLACEHOLDERS>
- evidence: <paths/content/digests, refs, gate URLs/results, or UNKNOWN>
- assessment: <reason, remaining criteria, next action>
```
