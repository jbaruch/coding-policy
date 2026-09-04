---
name: herdr-teamlead
description: >
  Run one task round as the team lead of three coding agents inside Herdr
  (https://herdr.dev): measure each worker's subscription headroom, assign the
  developer / tester / reviewer roles to the workers that can afford them,
  manage each worker's context and send it a self-contained role brief, wait for the
  report files, gate the round, dispatch a non-rotating judge on a dispute,
  and hand the merge to the `release` skill. Use ONLY when the
  user wants to run a round across the three Herdr worker panes: dispatch a
  task to the agent team, assign roles to the Herdr agents, load-balance the
  agents by usage or headroom, brief the developer / reviewer / tester, or
  collect the workers' reports. Requires HERDR_ENV to be set; without it this
  skill does not apply. Never use it for a single agent working a task on its
  own, however careful that work needs to be — an isolated edit, fix, review
  or investigation is not a team round.
---

# Herdr Team Lead Skill

Process steps in order. Do not skip ahead.

You are the lead of three rotating coding agents running in Herdr panes: a
developer, a reviewer/architect, and a tester — plus a non-rotating judge for
disputes. You assign the roles, write the briefs, read the reports, and gate
the round. You never edit the shared checkout, and you never write code for a
worker.

A round runs in two phases:

- **Phase 1, pre-development** — optional. The architect writes a design note,
  the tester writes a test plan, the developer implements and pushes.
- **Phase 2, post-push verification** — mandatory. The reviewer reviews the
  pushed branch, the tester verifies it. Both report against the current branch
  tip, and the release hand-off reads those reports alone.

Herdr command surface, worker quirks, and the usage-parsing details:

```text
skills/herdr-teamlead/references/herdr.md
```

Role weights, report reading, and the pre-PR review sequence:

```text
skills/herdr-teamlead/references/round-flow.md
```

## Step 1 — Determine the Mode

There are two modes, and `HERDR_ENV` is what tells them apart. Read it before
running anything.

**Standalone — `HERDR_ENV` unset or empty.** No Herdr session is around you.
This skill does not apply. Say so in one line and do the task yourself: no
roster, no briefs, no worktree provisioning, no report files, and no
pretending to be three agents in turn. Do not run the scripts below to
confirm what the variable already told you. Finish here.

**Herdr team round — `HERDR_ENV` set.** You are the lead of three worker
panes. Proceed to Step 2.

Even in Herdr mode, a task with nothing to hand to three workers is not a
round: a single edit, a question, a lookup, a review of work already done.
Those are yours to do directly.

## Step 2 — Verify Herdr and the Roster

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/roster.sh
```

Takes no arguments. Emits `{"caller":{"pane_id":...},"agents":[{"name","kind","pane_id","state"}]}`,
listing every named live agent other than your own pane.

- **Exit 0 with a populated `agents`** — proceed to Step 3.
- **Exit 0 with `agents: []`** — nobody is named. Report the unnamed panes from
  `herdr agent list` and the `herdr agent rename <pane-id> <name>` command that
  fixes it, then finish here.
- **Exit 1** — the precondition failed (outside Herdr, or `herdr` absent).
  Report the message verbatim and finish here.
- **Exit 2** — herdr failed. Report the message verbatim and finish here.

Fewer named workers than roles is a decision, not a detail: either name another
agent or fold two roles onto one worker in that worker's brief, and say which
you did.

## Step 3 — Verify Authority for the Repo

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/verify-authority.sh <owner/repo>
```

Emits `{"repo","viewer_login","owner_login","owner_type","viewer_permission","namespace_owner","authorized"}`.
`authorized` reflects namespace ownership alone; write permission never sets it
(`rules/external-repo-contributions.md` Default Deny).

- **`authorized: true`** — record the authority line for the briefs:
  `owner of <owner/repo>`. Set `EXTERNAL_PERMISSION` to `none`. Proceed to
  Step 4.
- **`authorized: false`** — the operator does not own this repo. Ask the
  operator for permission naming the repo AND each action type, then record
  their exact words in `EXTERNAL_PERMISSION` and set the authority line to
  `not owner; permitted this round: <their words>`. Without that answer, the
  round is read-only: compose briefs that forbid every external write, or stop.
  Never compose a brief that claims authority the operator did not give.
- **Exit 1** — a precondition failed: usage, `gh` or `jq` absent, or `gh` not
  logged in. Report the message verbatim, finish here.
- **Exit 2** — GitHub could not answer. An unanswerable question is not a
  permission. Report it and finish here.

Proceed immediately to Step 4.

## Step 4 — Measure Headroom

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh measure
```

Sends each configured worker its own usage command and parses the reply. Emits
one snapshot document — per agent: `kind`, `state`, `herdr_state`,
`state_source`, `windows`, `credits`, `plan`, `headroom_pct`, `skipped` — and
appends it to the state file (`skills/herdr-teamlead/state-schema.md`). A worker that
is `working` or `blocked` is reported as skipped with null windows, never
interrupted. A `working` verdict is confirmed against the pane before it counts:
`state_source` names which signal decided, `herdr` or `probe`, and `herdr_state`
carries what herdr claimed. Exit 1 means at least one agent could not be
measured; the snapshot still prints and names it in `failed_agents`.

The usage marker is confirmed in the text that gets parsed, never in a wait
alone. `--marker-poll-attempts` and `--marker-poll-interval` bound the
confirming poll; `--marker-timeout` and `--lines` size the pane read. Attempt
counts and intervals default to the script's own constants; see
`skills/herdr-teamlead/teamlead/measure.py`.

Add `--trace` (or `TEAMLEAD_TRACE=1`) when a live run does something the JSON
does not explain: every herdr invocation, its exit status, and its output go to
stderr, and stdout stays the machine-readable document. Traced fields are
redacted for credential shapes and capped per field with a `[truncated N bytes]`
marker; the shape list and the cap are constants in
`skills/herdr-teamlead/teamlead/herdr.py`.

Report a `failed_agents` entry to the user and measure that worker by hand
before relying on its role. Proceed immediately to Step 5.

## Step 5 — Plan the Roles

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh plan \
  --roles developer,tester,reviewer \
  [--exclude <role>=<agent>[,<agent>...]]...
```

Pure computation over the newest snapshot plus the assignment ledger. Contacts
no agent, writes nothing. Emits `{"assignments":{"<role>":"<agent>"},"rationale":[...],"snapshot_ref":{...}}`.
Exit 1 names the reason it could not plan.

`--exclude` bars agents from one role and repeats, once per role. Phase 2 bars
the author of the branch from `reviewer` and `tester` (`rules/agent-team-operation.md`
Review Before PR). Exit 1 covers an exclusion naming a role outside `--roles`,
and an exclusion set no assignment satisfies.

For a retained fix, plan `--roles developer` and exclude every other rotating
worker from that role. Use the task's existing developer, not a new headroom
winner. Plan the reviewer and tester separately for post-push verification.

`--roles` keys the output document. `role_costs` in config.json re-weighs a
seat per install. The weights, fill order, and tie-breaks are the planner's
contract; see `skills/herdr-teamlead/teamlead/planner.py` — the `plan`
docstring and `DEFAULT_ROLE_COSTS`.

Save the output to a file for Step 6. Relay the `rationale` lines to the user
as the round's role announcement: they name the weight behind each seat, the
exclusions applied, and any worker whose headroom reading is stale. Proceed
immediately to Step 6.

## Step 6 — Compose the Briefs

Write a values file for the round, then compose:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/compose-briefs.sh \
  .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/templates \
  <values.json> <round-reports-dir>
```

The values file is `{"shared": {...}, "roles": {"<role>": {...}}}`; a role's
own value beats the shared one. Emits
`{"common":"<path>","briefs":{"<role>":"<path>"}}`. Exit 2 means validation
failed and nothing was written — an unfilled placeholder, a supplied key no
template uses, a value that is not text, or a `REPORT` longer than the
script's limit (the worker's `REPORT: <path>` line must fit one pane row for
Step 10 to confirm it; use a short reports directory). Exit 3 means the placeholder scan
itself failed, so whether the briefs are clean is unknown: re-run, never
dispatch on it. The placeholder set and both validation directions are the
script's contract; see the header of
`skills/herdr-teamlead/compose-briefs.sh`.

What you decide, and it is the whole of your job here:

- `SHARED_CHECKOUT` — the checkout the workers read.
- `AUTHORITY_STATEMENT` and `EXTERNAL_PERMISSION` — verbatim from Step 3. Never
  a claim you composed yourself.
- Per role: `ISSUE`, `BRANCH`, `WORKTREE`, `REPORT`, `REPORTS_DIR`, and the
  phase and mode that role runs this round.

| Phase | Role | Mode | Output |
| ----- | ---- | ---- | ------ |
| 1 | reviewer | A | design note on the issue |
| 1 | tester | A or B | test plan, or acceptance tests as a patch |
| 1 | developer | — | implementation, pushed branch, no PR |
| 2 | reviewer | B | COMMENT review of the pushed branch |
| 2 | tester | C | gates plus acceptance tests against the pushed branch |
| 3 | release | — | PR opened, bot rounds answered, merged, branch deleted |

Phase 2 briefs name the branch AND the commit SHA the worker must report
against. A report against an older tip does not gate anything.

Name the issue, file, finding, and report path in full in every brief.
Context retention is limited to the same-role fix rounds in
`rules/agent-team-operation.md` Fix Loops. Fresh-worker fix briefs include
the prior attempt count and the ownership handoff that section requires.
Reviewer and tester verification briefs name `full` or `scoped` review,
the prior findings, and the follow-up issue for new advisories. The final
release-gating verification is `full`. Proceed immediately to Step 7.

## Step 7 — Provision the Worktrees

One call per worker that writes anything:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/provision-worktree.sh \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits `{"path","branch","base_ref","state"}`, where `state` is `created`,
`attached`, or `already-provisioned`. Exit 1 is a precondition (an invalid
branch name, a path outside the worktree root); exit 2 means git refused, or
the path holds something else. Branch-name and path rules are the script's
contract; see the header of
`skills/herdr-teamlead/provision-worktree.sh`.

The lead provisions every worktree a brief names, so a worker never runs git
against the shared checkout (`rules/agent-team-operation.md` Writers and
Checkouts). A read-only Phase 1 reviewer needs none. Remove them per
`rules/agent-worktree-isolation.md` Cleanup once the branch lands.

On any non-zero exit, fix the input it names and re-run this step; do not
dispatch a brief whose worktree does not exist. Proceed immediately to Step 8.

## Step 8 — Label the Layout (optional, once per team)

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/label-workspaces.sh \
  <lead-label> [<agent>=<workspace-id>]...
```

Names the lead's workspace, each worker's workspace after its agent, and each
worker's pane after its kind. With no pairs, the workspaces come from the
roster. Emits `{"lead":{...},"agents":[...]}` with a per-target
`renamed|unchanged|failed`. Exit 3 means at least one rename failed and the
JSON says which. A label failure never stops a round.

Run this once per team, not once per round: a name already in place is
reported `unchanged` and nothing is sent. Skip it on a team whose sidebar is
already named. Proceed immediately to Step 9.

## Step 9 — Dispatch the Briefs

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --no-clear]
```

Hands each worker its brief using the selected context mode. Emits one JSON
object: per role a record carrying `cleared`, `clear_reason`, `task`,
`fix_round`, `landed`, `started`, and `status`.

Keep the same `--task` identifier from initial development through all its
fixes. Omit `--fix-round` on the initial assignment; supply it on every fix.
Dispatch a retained fix as a developer-only assignment with
`--retain-context`. Other roles receive separate, cleared assignments.
The utility refuses an invalid mode or an exhausted fix counter before any
Herdr operation. Live fixes must advance the task's confirmed history;
retention also requires the same worker's matching prior assignment.
Missing or migrated history cannot authorize it. If context is unavailable,
stop the retained path and report the loss; do not reset the fix counter.

Each outcome names where the round goes next. Only a dispatched worker can
produce a report, so Step 10 waits on exactly the roles that landed here.

- **Exit 0** — every role was dispatched. Proceed to Step 10.
- **Non-zero with a busy target** — the whole round was refused before any
  keystroke went out. Nothing was dispatched, so there is nothing to wait for:
  wait for that worker to reach idle and re-run this step, or re-run it with a
  plan that omits the busy worker. Do not go to Step 10.
- **Non-zero with `"status": "sent_but_not_started"`** — the message was sent
  and no turn began for that role. Read that worker's pane; do not re-dispatch
  on top of it. Go to Step 10 for the roles whose records say `started`, and
  treat this role as producing no report this round.
- **A worker failed on its clear command** — its assignment was never sent, and
  the round is short that role. Go to Step 10 for the rest; re-dispatch this one
  by re-running this step for that role alone once its pane is clear.
- **A refusal saying the screen did not change** — the clear was unconfirmed;
  no brief was sent to that worker. Read the pane and clear it by hand.
  Re-run with `--no-clear` once the pane shows a fresh session. Fresh fix
  rounds that require an automatic clear must re-run without that flag.
  `--no-clear` records `cleared: false, clear_reason: hand`;
  `--retain-context` records `cleared: false, clear_reason: retained`.
  Never retain the previous task's context or retain across a role change.
- **A refusal naming an unaccounted composer** — the worker's input line holds
  text the lead did not send. Nothing was dispatched for that role. Read the
  pane and clear it by hand, or re-run this step with `--allow-recovery` once
  you know whose text it is. Codex sends no recovery key at all: its clear key
  exits an idle Codex.
- **A refusal the message does not cover** — report it verbatim and finish
  here. A dispatch nobody understands is not a round to wait on.
- `--dry-run` prints the context choice and commands without Herdr calls or
  state writes. It validates the mode, not retained history or live readiness.
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

Proceed to Step 10 with the roles that were dispatched.

## Step 10 — Wait for the Reports

One call per dispatched worker, in the order the round needs them:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/wait-report.sh <agent-name> <report-path>
```

Emits `{"agent","state","report_path","found","elapsed_seconds"}` on every
outcome except exit 2, which leaves stdout empty and puts its diagnostic on
stderr. Exit 4 adds `reason`. Completion requires both the report file on disk and the `REPORT: `
marker in the worker's pane. A single `idle` or `done` observation is not
completion. Poll interval and give-up budget are the script's own constants.

- **Exit 0** — the report is there. Continue to the next worker, then Step 11.
- **Exit 1** — the budget ran out. Read the pane with
  `herdr agent read <name> --source visible`. If the worker is still working,
  re-run this step for it: the script's own budget applies again, never a
  number chosen here. Otherwise go to Step 11 recording that it produced no
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
    once. Exits 0–3 from that re-run take their branches above. A second
    exit 4 is terminal: record the worker as producing no report and
    continue to the next worker.
  - The state is `idle` or `done` — record the worker as producing no report
    and continue to the next worker. Never re-dispatch on top of it. The
    cause is a report path too long for one pane row; Step 6's compose gate
    refuses those, so this outcome means a brief bypassed it.
- **Exit 3** — the worker is blocked at an approval or question dialog,
  confirmed across two reads and the pane. Read the dialog with
  `herdr pane read <pane-id> --source visible`, relay its text to the operator
  verbatim, and stop the round for that worker. You never answer it: the
  operator does. Resume only once `herdr agent get <name>` reports a state
  other than `blocked`, then re-run this step for that worker.

Proceed to Step 11 once every dispatched worker has been waited on, or once you
have recorded which of them produced no report.

## Step 11 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.

- **Any blocking finding** — follow `rules/agent-team-operation.md` Fix Loops.
  Read this task's confirmed fix history, name the next fix number, and return
  to Step 4 with self-contained briefs carrying the findings and prior
  reports. Preserve the developer for retained fixes; use a fresh context
  for the fresh-worker stage. Never reset the counter during re-planning.
  At the cap, a contested verdict, or a lead override, go to Step 12 first.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

The release hand-off has one condition, and all four parts are required:

1. The developer's report names the branch and the commit SHA it pushed.
2. A broad reviewer **Mode B** report reviews that same SHA and carries no
   blocking finding.
3. A broad tester **Mode C** report verifies that same SHA, with the repo's
   gates run and every acceptance criterion met.
4. Nothing has been pushed to the branch after those two reports.

A Phase 1 design note or test plan does NOT satisfy 2 or 3
(`rules/agent-team-operation.md` Review Before PR). A report against an older
SHA does not either — re-run Phase 2 against the current tip.
After scoped re-checks close the findings, re-run Phase 2 with `full` briefs
before handing off to release. Scoped reports alone never satisfy this gate.

With all four met, proceed immediately to Step 12.

## Step 12 — Compose the Judge Brief

Optional. Triggers and the ruling contract are in
`skills/herdr-teamlead/references/round-flow.md` "The Judge" (a bot
disagreement inside Step 19 returns here first). No trigger — proceed to
Step 19.

Compose the brief from `templates/brief-judge.md` through Step 6: the
dispute, both positions with report paths, the governing rule, the tree. Skip
Step 7 for the read-only judge. Proceed immediately to Step 13.

## Step 13 — Re-measure the Shared Window

Step 4's snapshot is a hint, not authority (`rules/stateful-artifacts.md`).
The judge's affordability is decided from a fresh reading, never from it.

Re-run Step 4's `measure`. That step's outcomes govern this run unchanged. A
`measure` that cannot read the judge worker's window is a stale-state failure,
resolved there before a ruling is planned.

Hand the fresh snapshot to Step 14 and plan nothing here. Proceed immediately
to Step 14.

## Step 14 — Plan the Judge Seat

Run Step 5's `plan` with `--roles judge`, against the Step 13 snapshot. It
names the worker the `judge` block pins, and refuses the round when that
window cannot cover a ruling:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh plan \
  --roles judge --snapshot <step-13-measure-output>
```

- **Exit 0** — the plan file names the judge worker. Proceed to Step 15.
- **Non-zero naming the judge's headroom** — the window cannot cover a
  ruling. Report it and finish here. There is no substitute judge, no
  fallback to another model, and no degraded ruling.
- **Any other non-zero** — a malformed config, a pinned worker absent from
  the snapshot, an unreadable snapshot, or a plan nobody can fill. Report the
  diagnostic verbatim and finish here. Do not hand-write an assignment to
  work around it.

The plan's `judge` object carries the seat's tier — `agent`, `model` and
`effort`, straight from the `judge` block. Step 15 launches the worker from
those values. Proceed immediately to Step 15.

## Step 15 — Start the Judge Worker on Its Pinned Tier

Run:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/start-judge-worker.sh \
  <step-14-plan-file> <pane> [claude|codex]
```

It reads the tier from the plan's `judge` object, starts the worker on it,
and refuses unless the pane's startup banner echoes that tier. Its argument
contract, exit codes and banner predicate are in the script header.

- **Exit 0** — the banner proved the tier. Its JSON names the agent, model
  and effort. Proceed immediately to Step 16.
- **Any non-zero** — report the script's diagnostic verbatim and finish here.
  A worker whose tier is unproven does not get briefed, and no tier is set by
  hand to work around it.

## Step 16 — Dispatch the Judge

Dispatch under Step 9's outcome contract exactly, passing the plan file from
Step 14 rather than a hand-written mapping:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief judge=<round>-judge.md \
  --common <path-to-COMMON.md> \
  --task <round>
```

Step 9's outcomes govern this dispatch unchanged. Proceed immediately to
Step 17.

## Step 17 — Wait for the Ruling

Wait with Step 10's `wait-report.sh <agent> <report-path>`, where `<agent>` is
the worker the Step 14 plan named. Proceed immediately to Step 18 once the
report lands.

## Step 18 — Act on the Ruling

The `RULING:` line binds the round. Only the operator overrides it.

- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing no branch content**
  — record the ruling. Proceed to Step 19 only with Step 11's broad reports
  against the current tip. Otherwise re-run Phase 2 with full briefs carrying
  the ruling. Do not re-dispatch the judge for the same settled dispute.
- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing the branch** — the
  lead never edits the branch itself. At an exhausted fix cap, report BLOCKED
  with the ruling and proposed plan to the operator; finish here. Otherwise
  return to Step 11 carrying `ACTION:` verbatim as required work. Count that
  implementation as the next fix, under the same task identifier, and gate
  the resulting tip again before release.
- **`blocked`** — the judge declined to rule. Stop the round and put its
  named question to the operator. Do not dispatch a second judge and do not
  rule in its place. Finish here.

## Step 19 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 6 with the role `release` for
the developer's agent (template `templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 7 (it reports
`already-provisioned`), dispatch through Step 9 so the context is cleared and
the brief is fresh, and wait on the report in Step 10. The worker merges. You
do not. Proceed immediately to Step 20 after its report.

## Step 20 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`. Proceed immediately to Step 21.

## Step 21 — Log the Round

Log the round: the assignments, the report paths, the findings, and the
outcome. If the round produced no findings at all, log it and say so in one
line rather than reproducing the reports. Finish here.

For the daily standup, which is a different round shape entirely, use
`Skill(skill: "herdr-standup")`.
