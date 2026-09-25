---
name: herdr-teamlead
description: >
  Run Herdr rounds as a nonworking foreman: assign, supervise, accept or
  reject, never do the crew's work. Covers on-demand specialists, model tiers, bounded briefs,
  report verification, and release gates. Use for requests to dispatch the Herdr
  team, balance worker usage, collect reports, run or retrieve retrospectives,
  catch up on outstanding user attention, curate team lessons, or save and resume
  foreman handoffs. Live rounds require HERDR_ENV; saved memory and attention work
  offline. Other standalone tasks skip this skill.
---

# Herdr Foreman Skill

Process steps in order. Do not skip ahead.

Before any finish with enrolled work, reconcile the whole fleet under
`references/supervision.md`. Continue observation or persist an authorized pause
or handoff covering every active assignment. Keep user attention visible under
`references/attention.md`.

Follow `rules/agent-team-operation.md` for round constraints.

Each command resolves `CP` to the local or home plugin. Repeat its resolver in
every call. Prose `skills/...` paths are relative to that root.

Before each decision, load its records and read every listed file:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" load-set --decision <plan|brief|gate|diagnose> --task <task>
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" load-set --decision wake --enrollment <enrollment-id>
```

| Decision | Step | Target |
| --- | --- | --- |
| `plan` | Step 5 | `--task` |
| `brief` | Step 7 | `--task` |
| `wake` | Step 11, per `wake` event | `--enrollment` |
| `gate` | Step 12 | `--task` |
| `diagnose` | Step 13, diagnosis mode | `--task` |

It prints JSON whose `files` list is the floor for that decision; a file with
`present: false` is a gap to resolve, not one to skip. A non-zero exit names a
refused task, enrollment or unreconciled dispatch on stderr; resolve it first.
The contract is `skills/herdr-teamlead/references/working-memory.md` "Load a
decision's records".

- Run each step's commands as that step documents them
- Never write a throwaway helper script in a scratch directory
- Never name a scratch file in a handoff
- A command sequence you repeat across tasks belongs in a tested script
  shipped with this skill; record it as a follow-up rather than scripting it
  locally

References:

```text
skills/herdr-teamlead/references/herdr.md
skills/herdr-teamlead/references/round-flow.md
skills/herdr-teamlead/references/round-setup.md
skills/herdr-teamlead/references/task-ledger.md
skills/herdr-teamlead/references/retrospectives.md
skills/herdr-teamlead/references/working-memory.md
skills/herdr-teamlead/references/attention.md
skills/herdr-teamlead/references/supervision.md
skills/herdr-teamlead/references/assignment-reasoning.md
skills/herdr-teamlead/references/specialists.md
skills/herdr-teamlead/references/judge-round.md
skills/herdr-teamlead/state-schema.md
```

## Step 1 — Determine the Mode

Three request kinds are answered offline, need no live Herdr, and finish here
after the requested operation. Each reference carries its own owner commands and
their contracts. Use the recorded state override or default, report any non-zero
diagnostic, and never fabricate missing history. They grant no new task
authority.

- **Catch-up or saved attention** — `references/attention.md`. Read all
  attention pages before claiming completeness.
- **Lesson curation, saved foreman context, or a foreman handoff** —
  `references/working-memory.md`.
- **A saved retrospective** — `references/retrospectives.md`. Report the note's
  date, coverage, conclusions, and path.

For every other request, read `HERDR_ENV` before running scripts.

- **Unset or empty** — this skill does not apply. Say so and do the task
  directly, without roster calls, briefs, provisioning, reports, or simulated
  worker roles. Finish here.
- **Set, with a team task or new retrospective** — Proceed to Step 2.
- **Set, with a lookup, a file inspection, research, a bounded question, a
  review of existing code, a repository edit, or any other task deliverable** —
  a round, whatever its size. None of it is foreman work. Staff it in Step 5.
  Proceed to Step 2.
- **Set, none of the above applies, and the answer is already in the foreman's
  context** — say it. Finish here.

## Step 2 — Run the Round Preflight

One call answers every deterministic check a round start owes: Herdr and the
roster, authority for the repo, measured headroom, the capability table's
cadence, and worktree hygiene.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/round-preflight.sh" \
  --repo <owner/repo> --checkout <shared-checkout>
```

Emits one JSON object: `ready`, the `blocking` reasons, the cadences that are
`due`, and each check's own payload under `checks`. Exit 1 is a verdict, not a
failure — something blocks the round. Exit 2 means the preflight could not
answer.

- **Exit 0** — read `due`, satisfy any cadence it names, and proceed to Step 5.
  When `due` names the capability table, dispatch the refresh consultation under
  `references/model-tiers.md`, then record its report and show the result:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" capability-record --record <report.json>
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" capability-show
```

- **Exit 1** — report the `blocking` reasons verbatim. Each names the command
  that produced it; re-run that one, not the preflight.
- **Exit 2** — report the diagnostic and finish here.

Which checks run, and which exit codes they fold into `blocking`, are the
script's decision contract — see `skills/herdr-teamlead/round-preflight.sh`, not
restated here (`rules/script-as-black-box.md`).

Steps 3 and 4 remain the individual commands, for a caller that needs one on its
own. A round start runs this instead of all of them. The roster has no step of
its own; inspect it directly when only the live workers are wanted:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Emits the caller and live workers with kind, pane, and state. An empty roster on
exit 0 means unnamed panes: report them from `herdr agent list` with the
correcting `herdr agent rename <pane-id> <name>` command.

Record staffing gaps under `references/round-setup.md`. Leave unused specialist
profiles unlaunched. Never duplicate targets or fold verification onto a
contributor. Start workers in YOLO mode under `references/model-tiers.md`;
preserve it on relaunch. Verify live permission flags before dispatch, including
existing workers. Record task authorization and permitted actions under the
round-setup reference. Create or resume the stable ledger under
`references/task-ledger.md`; record its absolute path before dispatch. Apply the
round-setup accepted-behavior, resume and supervision binding requirements.

Run `references/retrospectives.md` on resume, before planning, or for an
explicit retrospective request. For an explicit request, complete a new
retrospective and finish here.

Proceed immediately to Step 5.

## Step 3 — Verify Authority for the Repo

Step 2 runs this. Use it alone when only the authority answer is wanted.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/verify-authority.sh" <owner/repo>
```

Record the emitted namespace ownership evidence using Step 3 of
`references/round-setup.md`. For a non-owned repo, reuse explicit per-action
operator permission; absent permission, remain read-only or finish here.
On non-zero, report the diagnostic and finish here.

Proceed immediately to Step 4.

## Step 4 — Measure Headroom

Step 2 runs this. Use it alone to re-measure, which the judge round does.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure
```

Emits and saves headroom, windows, state, `tier_billing`, and `failed_agents`.
Busy workers are skipped. Unmeasured billing stays `unknown`. Report failed
measurements and obtain their readings before relying on those seats.

Usage and `--trace` contracts:

```text
skills/herdr-teamlead/references/round-setup.md
```

Proceed immediately to Step 5 once the required readings are available.

## Step 5 — Plan the Roles

Read the open tasks waiting for a seat, oldest first:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" foreman-queue
```

It prints `{"schema_version": 1, "queue": [...]}`. Each entry names `task`,
`waiting_for` (a list of `developer`, `reviewer` or `tester`),
`dispatched_seats`, `since`, `developer` and `fix_round`. A non-zero exit names
an unusable state file on stderr; restore it before planning. Tasks with an
active worker are omitted. A partitioned verifier stays listed with its
dispatched slices; check them against the validated partition. The order is a
default; choose another when the round needs it.

Choose the responsibilities needed next under `references/specialists.md`.
Supply its requirements file for specialized work. Schedule consultation and
verification as the task needs them. `plan` bars a developer reserved to
another task and a worker with an active enrollment, and names each bar in its
`rationale`; do not pass `--exclude` for either. `apply` re-reads the
reservations before sending. Reusing a reserved developer elsewhere requires
closing its task first.

The composition triggers decide part of that roster. Classify this round
against the repo's declaration first. For a pre-implementation round, pass
`--planned` naming the surfaces the work will touch. A round that writes no
repository content — an investigation, an architecture or advisory consultation
— declares `writes_repository: false` in that file instead
(`references/specialists.md`). A round with work already written classifies
that work:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" detect-triggers \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--planned <planned.json>] [--decisions <decisions.json>]
```

Exit 0 means every fired trigger is staffed or answered. On exit 1, read the
stderr object: an absent declaration is written first (`references/specialists.md`),
and an `unaddressed_trigger` is staffed in the roles below or answered by a
recorded decision with its reason. Re-run the command with the updated
declaration, roles, requirements and decisions after every such change, and
plan only once it exits 0.

A round that will split its review surface validates the partition first, then
plans it with `--partition`:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" validate-partition \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --partition <partition.json>
```

Exit 1 names every unowned path, every overlap and every slice owning nothing,
in one run. Fix the partition and re-run; plan only once it exits 0. Save its
stdout — `plan --partition` takes that result, never the document
`validate-partition` read. Validate at the pushed head: the result's `proof`
records the repo, base and head it was proven against, and Step 12's gate
cannot check a working-tree proof. Format, ownership
payload and the seating it produces:

```text
skills/herdr-teamlead/references/review-partition.md
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--exclude <role>=<agent>[,<agent>...]]... [--partition <validated.json>] \
  [--judge-mode adjudication|diagnosis] \
  [--round <role>=<round-type>] [--round-context <evidence.json>] \
  --task <task-id> [--fix-round <N>] [--correction-plan <id> --work <work.json>]
```

Emits the role plan without worker contact; a partitioned role is seated once
per slice as `<role>#<slice>`, and Step 10 dispatches each seat with its own
brief. A judge seat declares its mode:
`adjudication` rules on a contested verdict, `diagnosis` on the investigator's
assessment at an exhausted allowance. Pass the same `--judge-mode` to `apply`.
On exit 1, resolve the diagnostic before continuing. Apply the Step 5 constraints in `references/round-setup.md`:
exclude contributors from verification, reserve the developer through early fixes,
preserve task identity and fix count, and reuse recorded correction bounds.
Tier contracts:

```text
skills/herdr-teamlead/references/model-tiers.md
skills/herdr-teamlead/references/dispatch-recovery.md
```

Save the plan and rationale.
Proceed immediately to Step 6.

## Step 6 — Build the Review Package

For reviewer/tester briefs, run from a checkout holding the recorded commits:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/review-package.sh" \
  <recorded-base-sha> <pushed-head-sha> <round-reports-dir>/review-<base7>..<head7>.diff
```

Apply the Step 6 base, range, and rebuild requirements in
`references/round-setup.md`. Success prints the absolute review-package path;
set it as `REVIEW_PACKAGE`. On non-zero, fix the diagnostic and retry before
composing verification briefs. Other roles need no package.
Proceed immediately to Step 7.

## Step 7 — Compose the Briefs

Resolve policy paths through the Step 7 reference first. Write its outputs in
`shared` within `{"shared": {...}, "roles": {"<role>": {...}}}` and run:

`GATES` is shared: Step 2's `checks.gates.detail.brief`, verbatim. On a
non-empty `checks.gates.detail.missing`, name those paths in the round's report.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/compose-briefs.sh" \
  "$CP/skills/herdr-teamlead/templates" \
  <values.json> <round-reports-dir>
```

Emits common and role-brief paths. On non-zero, fix the diagnostic before
dispatch. Validates composition inputs and review evidence before writing.
Use a fresh absolute report path per role and attempt.

Follow `references/round-setup.md` Step 7 for shared and role-specific values,
authority, review evidence and brief completeness.
Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

Step 2's preflight pruned, every round, and reported the result under
`checks.worktrees`. Report every kept `dirty`, `unmerged`, `locked` and
`detached` entry to the operator; never remove them by hand. Run it alone only
to re-prune:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/prune-worktrees.sh" <shared-checkout>
```

Emits the worktrees and branches removed, each kept one with its reason, and
`failed`; exit 2 lists every check or removal git refused. Exit 1 decided
nothing: fix its diagnostic and re-run before provisioning.

Then run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, base, and `created|attached|already-provisioned`. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only consultations need none. Clean up after merge per
`rules/agent-worktree-isolation.md`. Proceed immediately to Step 9.

## Step 9 — Label the Layout

Optional, once per team; skip an already named sidebar.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/label-workspaces.sh" \
  <lead-label> [<agent>=<workspace-id>]...
```

Emits per-target `renamed|unchanged|failed`; exit 3 names partial failures.
Report label failures and continue. Proceed immediately to Step 10.

## Step 10 — Dispatch the Briefs

Complete the retrospective reference's cadence and transition checks before live
dispatch. Apply rechecks coverage before worker input. Dry runs prove no coverage.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply \
  --assignments <plan-file> \
  --brief <role>=<path> [--brief <role>=<path>]... \
  --report <role>=<report> [--report <role>=<report>]... \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --retain-specialist | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

Emits dispatch JSON under `state-schema.md`. Supply each role's fresh absolute
report path from its brief. Apply enrolls before input; unknown sends remain
observation obligations. Apply refuses while an open decision or blocker on the
task is unanswered; see the attention reference's Dispatch gate.
Classify every brief against Step 3's authorization before sending it.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the recovery reference's Dispatch context requirements before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone. Warm consultations use the recovery reference's
`--retain-specialist` path. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

Follow the Dispatch Results contract in `references/round-flow.md` for busy,
uncertain, failed, and dry-run outcomes. Preserve all already enrolled work.

Dispatch references:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
skills/herdr-teamlead/references/model-tiers.md
```

Proceed to Step 11.

## Step 11 — Observe the Fleet

Run the bounded foreground watcher for all enrolled assignments:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" supervision-watch [--state <state-file>]
```

Retain and await its real execution handle. The JSON result gives `reason`,
`through`, and durable `events`; a quiet deadline completes only that checkpoint.

Then ask which of those events need you:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" supervision-gate [--state <state-file>]
```

It returns `wake` and `suppressed`, each event with its `reason`. Acknowledge
every `suppressed` event with that reason as its outcome, without reading
anything. Only named, information-poor cases are suppressed and every other
event wakes you, including a kind the gate has never seen; which cases, and
why, is the script's decision contract — see
`skills/herdr-teamlead/teamlead/supervision_gate.py`, not restated here
(`rules/script-as-black-box.md`).

For each `wake` event, verify report delivery for its enrollment:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" check-member --enrollment <enrollment-id> \
  [--worktree <worker-checkout>]
```

It reads the agent, report, recorded base and send time from the owner
records and runs `wait-report.sh --once` with them
(`skills/herdr-teamlead/teamlead/members.py`). Its JSON carries the
checkpoint's `exit` and delivery JSON as `wait`:

- `exit` 0 confirms delivery, 1 remains pending, 3 confirms blocked, 4 lacks
  confirmed delivery, and 5 proves terminal refusal; record it with
  `record-refusal`
- The command itself exits non-zero when the dispatch has no recorded send
  time, or when the wait ran without a verdict (`wait_failed`, carrying the
  wait's own exit, 2 included); resolve the diagnostic stderr names, then run
  it again Read delivered reports in full. Pass the worker's checkout
as `--worktree` when it has one. An exit 1
then carries either `reason: checkpoint_pending` or a `stall` object; act on a
stall under `rules/agent-team-operation.md` Stalled Workers and record the
obligation through `references/attention.md`. Preserve the blocked/refusal and native-recovery paths in the
following references; never re-dispatch over uncertainty or resend a refused
brief to its provider.

```text
skills/herdr-teamlead/references/supervision.md
skills/herdr-teamlead/references/dispatch-recovery.md
```

Save a consultation's successful delivery receipt and record `assess-specialist`
under `references/specialists.md` before retiring its enrollment.

Record each outcome in the task ledger and user-facing obligations in the
attention queue. Acknowledge only handled event IDs through the saved snapshot;
schedule pending rechecks. Once the ledger records an assignment's assessed
outcome, close its enrollment in one call:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" close-member --enrollment <enrollment-id> \
  --ledger <absolute-TASK-LEDGER.md>
```

It refuses until the ledger's latest event for that worker and report carries
an assessed decision, then acknowledges the enrollment's pending events and
resolves it, citing that ledger event; a repeat replays. Resolution stays
separate from assignment acceptance and task completion. Complete due
retrospectives between checkpoints without interrupting workers. Resume the fleet
watch while any observation obligation remains; one blocked worker never hides
another worker's report. Proceed to Step 12 when the required reports are delivered
or their unavailability and recovery are recorded.

## Step 12 — Gate the Round

Annotate every delivered report in one call before reading any of them:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/classify/classify-reports.sh" <report>...
```

Each label carries a verdict from the answer set in
`skills/herdr-teamlead/classify/report-verdict.schema.json` and the sentence
that decided it. Read the reports together and gate them in one turn,
not one turn per report. A label is advisory. It never replaces the full read,
and a report in `unannotated` is read exactly as it would have been. Look twice
where a label disagrees with your own reading. Which vendor and model it uses, and its measured accuracy,
are the script's contract — see `skills/herdr-teamlead/classify/classify-report.sh`.

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.
Before accepting a mechanical round, compare its whole result against the
oracle its plan declared. `<result-file>` is the pushed diff for a `patch`
oracle and the produced output otherwise:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" verify-oracle \
  --plan <plan-file> --role <role> --result <result-file>
```

Exit 0 is a match. Exit 1 with `"match": false` is a blocking finding on the
round; exit 1 with no verdict is a usage error to resolve before gating.
Before accepting a partitioned responsibility's pass, confirm its plan, as
dispatched, still covers exactly the task's diff at the tip under review:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" verify-partition \
  --plan <plan.json> --repo <repo-path> --head <tip-under-review> --task <task>
```

- Exit 0 confirms it
- Exit 1 names what failed: another repo or base, a stale head, an edited
  boundary, a seat never dispatched or dispatched with another boundary, or
  paths the slices leave unowned, no longer cover, or own twice
- On exit 1, re-validate the partition at the tip
- Then replan from that result
- Then review the slices again

Record assignment acceptance or outstanding work in the task ledger against
the inspected report and artifact evidence. Record the task's gate decision
separately; a worker finishing its brief never completes the whole task.
Assess correction scope and bug evidence under `references/assignment-reasoning.md`.
Persist user-facing obligations under `references/attention.md` before presenting
them; record an actual answer or resolution separately from showing the item.

After accepting a consultation, return to Step 4 for the next needed
responsibility. For an investigation-only task, use the knowledge gate below.

For an investigation-only task, assess every assigned report against the requested
knowledge deliverable. Resolve blocking findings through the same bounded and
judge paths below. Once its criteria hold, present the findings and preserve open
user decisions; proceed to Step 15 if a task worktree needs cleanup, otherwise
Step 16. No implementation or release is inferred from the diagnostic result.

- **Any blocking finding** — apply the round-flow reference's Blocking Gate
  contract and `rules/agent-team-operation.md` Fix Loops. Return to Step 4 for
  an authorized correction or Step 13 for a required judge ruling. At an
  exhausted approach allowance, record the checkpoint through
  `references/dispatch-recovery.md`, consult the investigator under
  `references/specialists.md` with round context `{"investigator":
  {"diagnosis_input": true}}`, and take its assessed report to Step 13 for the
  diagnosis; no operator decision is awaited.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

Apply the release gate in this reference; obtain broad independent reviewer and
tester passes against the current pushed tip before release:

```text
skills/herdr-teamlead/references/round-flow.md
```

With its release criteria met, proceed immediately to Step 13.

## Step 13 — Run the Judge Round

Optional. No trigger — proceed to Step 14. A bot disagreement inside Step 14
returns here first.

Run the round's seven steps in order — compose the brief, re-measure the shared
window, plan the pinned seat, start its worker on the pinned tier, dispatch,
wait, act on the ruling:

```text
skills/herdr-teamlead/references/judge-round.md
```

The judge is read-only, so Step 8 is skipped for it. Never substitute a judge,
lower its tier, or hand-write an assignment to bypass a refusal. Its last step
names where to continue.

## Step 14 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 7 with the role `release` for
the developer's agent (template `templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 8 (it reports
`already-provisioned`), dispatch through Step 10 so the context is cleared and
the brief is fresh, and wait on the report in Step 11. A source-changing
release finding returns to Step 12 for the next counted developer assignment.
The worker merges after all gates pass. Proceed immediately to Step 15 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 15 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`, then run Step 8's prune script again
for the round's other worktrees. Proceed immediately to Step 16.

## Step 16 — Log the Round

Finalize the task ledger with the round outcome and remaining obligations.
Mark the task completed only after its acceptance criteria and required
release and cleanup obligations are verified. When the task merged or was
abandoned, close it. The record is
`{"task", "outcome": "merged" | "abandoned", "evidence"}`:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" close-task --record <close.json>
```

- **Exit 0** — stdout is the recorded `task_closed` event JSON. Repeating the
  same closure prints the existing event. Proceed.
- **Non-zero** — stderr names the refused field or the conflicting earlier
  closure. Correct the record and re-run; do not finish Step 16 with the
  task unclosed.

Preserve the ledger for resume and standup. Preserve retrospective notes and link them from the ledger. Save
current progress through the attention owner and stow the foreman's handoff under
the working-memory reference. Reconcile supervision before ending the turn.
Report outstanding attention first, followed by the outcome and saved paths.
Finish here.

For the daily standup, use
`Skill(skill: "herdr-standup")`.
