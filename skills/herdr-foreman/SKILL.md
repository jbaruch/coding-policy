---
name: herdr-foreman
description: >
  Run Herdr rounds as a nonworking foreman on a selected, verified tier:
  assign, supervise, accept or reject, never do the crew's work. Covers on-demand specialists, model tiers, bounded briefs,
  report verification, and release gates. Use for requests to dispatch the Herdr
  team, plan or run implementation, review, fix, judge, and release rounds,
  measure Herdr worker capacity, recover refused dispatches, restart or relaunch
  idle workers, collect reports, run or retrieve retrospectives,
  catch up on outstanding user attention, curate team lessons, save and resume
  foreman handoffs, or report a task's cost or resource use through acceptance.
  Live rounds require HERDR_ENV; saved memory, attention and cost reports work
  offline. Other standalone tasks skip this skill.
---

# Herdr Foreman Skill

Process steps in order. Do not skip ahead.

Before any finish with enrolled work, reconcile the whole fleet under
`skills/herdr-foreman/references/supervision.md`. Continue observation while
authorized work remains. Finish only after a genuine user pause or a verified
successor/reset continuation satisfies the Stop gate. A saved handoff hold
prepares reset preflight; it does not permit Stop on its own. Keep user
attention visible under `skills/herdr-foreman/references/attention.md`.

Before any team-round action outside Step 1's bounded factual lookup, read the
team-round contract in full. It is the
file `rules/agent-team-operation.md` points to, and it binds every step below.
Every worker brief names it as a required read.

```text
skills/herdr-foreman/references/team-operation.md
```

You run on the foreman's selected tier: the operator's `coordination` row,
resolved through `select_tier` and checked against the capability table
(`skills/herdr-foreman/references/team-operation.md` Foreman Seat).

Each command resolves `CP` to the local or home plugin, or to `.` in a
coding-policy clone; anywhere else it stops with an install instruction. Repeat its resolver in every call. Prose `skills/...` paths are relative to that root.

Before each listed owner decision, load its records and read every listed file:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" load-set --decision <plan|brief|gate|diagnose> --task <task>
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" load-set --decision wake --enrollment <enrollment-id>
```

| Decision | Step | Target |
| --- | --- | --- |
| `plan` | Step 5 | `--task` |
| `brief` | Step 8 | `--task` |
| `wake` | Step 11, per `wake` event | `--enrollment` |
| `gate` | Step 12 | `--task` |
| `diagnose` | Step 13, diagnosis mode | `--task` |

It prints JSON whose `files` list is the floor for that decision; a file with
`present: false` is a gap to resolve, not one to skip. A non-zero exit names a
refused task, enrollment or unreconciled dispatch on stderr; resolve it first.
The contract is `skills/herdr-foreman/references/working-memory.md` "Load a
decision's records".

- Run each step's commands as that step documents them
- Never write a throwaway helper script in a scratch directory
- Never name a scratch file in a handoff
- A command sequence you repeat across tasks belongs in a tested script
  shipped with this skill
- Record such a sequence as a follow-up
- Never script such a sequence locally

Load the reference named by the current step before acting. Read its contracts
in full; execute only the current step and its stated continuation. Do not preload
later stages or rerun earlier stages on resume. Step numbers in these references
match this execution plan.

## Step 1 — Determine the Mode

Delegation in either mode follows `rules/agent-team-operation.md` Delegation.
In a team round, use the existing Herdr owner route under
`skills/herdr-foreman/references/team-operation.md` Delegation.

A refusal naming the legacy `teamlead` default home is a one-time operator
step, not a mode: record it as a
user-attention blocker naming `foreman migrate-home` (`skills/herdr-foreman/state-schema.md` Home
Migration), and run nothing else until the operator has stopped every foreman
and run it.

Four request kinds are answered offline, need no live Herdr, and finish here
after the requested operation. Each reference carries its own owner commands and
their contracts. Use the recorded state override or default, report any non-zero
diagnostic, and never fabricate missing history. They grant no new task
authority.

- **Catch-up or saved attention** — `skills/herdr-foreman/references/attention.md`. Read all
  attention pages before claiming completeness.
- **Lesson curation, saved foreman context, or a foreman handoff** —
  `skills/herdr-foreman/references/working-memory.md`.
- **A saved retrospective** — `skills/herdr-foreman/references/retrospectives.md`. Report the note's
  date, coverage, conclusions, and path.
- **A task's cost or resource use** — run the read-only report, omitting
  `--task` for every task:

  ```bash
  CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
  bash "$CP/skills/herdr-foreman/foreman.sh" cost-report --task <task>
  ```

  It prints JSON with a `tasks` list and an `unrecorded` list. Relay each
  quantity separately, and every `unknown` value as `unknown`. Never total the
  quantities or claim a saving. A non-zero exit writes its diagnostic to stderr
  for an unusable state file or an unknown task; report it and stop. The output
  contract is `skills/herdr-foreman/state-schema.md` (Writer / Reader Contract).

For every other request, read `HERDR_ENV` before running scripts.

- **Unset or empty** — this skill does not apply. Say so and do the task
  directly, without roster calls, briefs, provisioning, reports, or simulated
  worker roles. Finish here.
- **Set, with a bounded factual lookup**
  - Read `skills/herdr-foreman/references/team-operation.md` Bounded Factual Lookup.
  - Answer within that boundary.
  - Cite the source.
  - Run no round preflight, roster measurement, enrollment, report gate or context reset for the lookup.
  - Finish here only with no enrollment requiring supervision or a valid Stop-gate condition; otherwise resume Fleet Supervision at Step 11.
- **Set, outside Bounded Factual Lookup, with a team task or new retrospective** — Proceed to Step 2.
- **Set, outside Bounded Factual Lookup, with a lookup, a file inspection, research, a bounded question, a
  review of existing code, a repository edit, or any other task deliverable** —
  a round, whatever its size. None of it is foreman work. Staff it in Step 5.
  Proceed to Step 2.
- **Set, none of the above applies, and the answer is already in the foreman's
  context** — say it. Finish here.

## Step 2 — Run the Round Preflight

Run capability migration and the consolidated round preflight. Relay the worktree
report and warnings verbatim. Exit 0 proceeds immediately to Step 5, or the stow's
continuation step on resume. Cadence-only maintenance and unrelated worktrees
never block the selected task. Exit 1 follows the named owner's recovery; exit 2
reports the diagnostic and finishes here. Preserve enrolled supervision obligations.

Read before acting:

```text
skills/herdr-foreman/references/round-preflight.md
```

## Step 3 — Verify Authority for the Repo

Step 2 includes this check. For a standalone authority check, run the documented
command and record namespace evidence. Non-owned repositories require explicit
per-action permission; otherwise remain read-only or finish here. On non-zero,
report the diagnostic and finish here. Proceed immediately to Step 4.

Read before acting:

```text
skills/herdr-foreman/references/round-preflight.md
```

## Step 4 — Measure Headroom

Step 2 includes this measurement. For a re-measurement, run the documented owner
command. Preserve failed measurements and unknown billing; recover required seats
through their owner route. Proceed immediately to Step 5 when required readings
are available.

Read before acting:

```text
skills/herdr-foreman/references/round-preflight.md
```

## Step 5 — Plan the Roles

Load the plan decision's records. Read the waiting queue, choose the needed
responsibilities, resolve composition triggers, validate any review partition,
and save the owner's plan and rationale. Preserve developer reservations, task
identity, cumulative fix count, correction bounds and independent verification.
Plan only after trigger and partition validation pass. Staff specialists when
the task's triggers or unresolved judgment require them, not every round.
Proceed immediately to Step 6.

Read before acting:

```text
skills/herdr-foreman/references/role-planning.md
```

## Step 6 — Build the Review Package

Build the recorded-base-to-pushed-head review package for reviewer and tester
briefs. Resolve any diagnostic before composition; other roles need no package.
Proceed immediately to Step 7.

Read before acting:

```text
skills/herdr-foreman/references/role-planning.md
```

## Step 7 — Provision the Worktrees

Route on Step 2's worktree check before provisioning. Provision every writing
worker and every checkout named in a brief; consultations may be read-only.
Preserve the task's original authorized base. Never dispatch a missing worktree
or manually remove another task's checkout. Proceed immediately to Step 8.

Read before acting:

```text
skills/herdr-foreman/references/assignment-delivery.md
```

## Step 8 — Compose the Briefs

Load the brief decision's records. Resolve policy paths and compose complete,
bounded briefs with fresh report paths and recorded authorization. Accepted
behavior, reproducible failure and a bounded correction permit a direct developer
brief. Unsettled behavior, evidence, scope, causal uncertainty or exhausted
allowances take their named consultation route; return to Step 5 when required.
The foreman never substitutes substantive judgment. Proceed immediately to Step 9.

Read before acting:

```text
skills/herdr-foreman/references/assignment-delivery.md
```

## Step 9 — Label the Layout

Optionally label the team layout once; skip an already named sidebar. Report
partial label failures and continue immediately to Step 10.

Read before acting:

```text
skills/herdr-foreman/references/assignment-delivery.md
```

## Step 10 — Dispatch the Briefs

Check retrospective cadence and transition coverage, then apply the saved plan
with the composed briefs and fresh report paths. Preserve task authorization,
identity and fix count. Apply enrolls before input; reconcile uncertain sends
before retrying. An applied send is not task acceptance. Unanswered task decisions
and blockers retain their dispatch gate. Proceed immediately to Step 11.

Read before acting:

```text
skills/herdr-foreman/references/assignment-delivery.md
```

## Step 11 — Observe the Fleet

Retain and await the bounded fleet watch's real execution handle, then gate its
events. A quiet deadline ends only the checkpoint. Acknowledge suppressed events
with their recorded reason. For each wake event, load its enrollment's records and
verify report delivery. Persist user attention and acknowledge only handled event
IDs; do not assess outcomes or close enrollments here. Continue watching every
observation obligation. Proceed to Step 12 when required reports are delivered or
their unavailability and recovery are recorded.

Read before acting:

```text
skills/herdr-foreman/references/fleet-checkpoint.md
```

## Step 12 — Gate the Round

Load the gate decision's records. Classify reports, read every report in full,
and record classifier gates before assessment. Contract lines decide acceptance;
labels and a clean worker exit never replace the verdict. Verify any mechanical
oracle and partition against the dispatched plan and current tip. Record assessed
assignment outcomes separately from the task's gate; close enrollments only after
the owner verifies the ledger and report evidence. Resume Step 11 for remaining
observation obligations.

Blocking verdicts take the bounded fix loop or a required Step 13 ruling. A
contradicting classifier gate or weighing nomination goes to Step 13. Exhausted
approach allowances go through the investigator's assessed report to judge
diagnosis, without waiting for an operator decision. Advisory findings go in the
round log; they do not become blocking verdicts. Never weigh findings yourself or
use a judge report to clear a verdict gate.

Every return to Step 4 crosses Steps 16 and 17 first, with the named continuation
saved in the stow. An accepted investigation presents findings without inferring
implementation or release, then goes to Step 15 if cleanup is needed, otherwise
Step 16. Release requires broad independent reviewer and tester passes at the
current pushed tip. With release criteria met, proceed immediately to Step 13.

Read before acting:

```text
skills/herdr-foreman/references/round-gate.md
```

## Step 13 — Run the Judge Round

No judge trigger: proceed immediately to Step 14. Otherwise run the referenced
judge round, preserving its pinned tier, independence and verified ruling.
Diagnosis loads the diagnose decision's records. Skip writing-worktree provision
for the read-only judge. After any ruling, log and reset through Steps 16 and 17
before its named continuation.

Read before acting:

```text
skills/herdr-foreman/references/round-completion.md
```

## Step 14 — Release the Pull Request

Release is a fresh assignment, not a prompt into the developer's existing
context. Follow the Step 7, 8, 10 and 11 release route with fresh report evidence.
Open verdict gates and source-changing release findings return to Step 12; bot
disagreements return to Step 13. Verify the reported release against live VCS
and release gates, record the evidence, then proceed immediately to Step 15.

Read before acting:

```text
skills/herdr-foreman/references/round-completion.md
```

## Step 15 — Clean Up the Worktree

After merge, fast-forward the shared checkout, remove only the merged task's own
worktree with `git worktree remove`, then delete its branch. Follow the mandatory
post-merge order in `rules/agent-worktree-isolation.md`. Re-sweep the round's other
worktrees through Step 7's referenced sweep contract. Proceed immediately to Step 16.

Read before acting:

```text
skills/herdr-foreman/references/round-completion.md
```

## Step 16 — Log the Round

Finalize the ledger and remaining obligations. Complete the task only after its
acceptance, release and cleanup requirements are verified. Close merged or
abandoned tasks through the owner; resolve any refusal before continuing. Keep
in-progress tasks open. Preserve lessons and retrospective notes; report outstanding
attention first, then the outcome and saved paths. Proceed immediately to Step 17.

Read before acting:

```text
skills/herdr-foreman/references/round-completion.md
```

## Step 17 — Reset the Foreman Context

Stow the handoff, required reads, gaps and the outcome's continuation step. Handle
pending supervision events and save handoff holds for active enrollments before
scheduling reset. A stow or handoff hold alone never permits Stop. Follow the
working-memory reset outcome contract; failed resets do not permit Stop with
active work.

The next context reads the stow and its required files, reconciles supervision,
reads the queue, runs Steps 1 and 2, then takes the stow's continuation step in
place of Step 5. The queue lists seats only; gating, release and closure resume
at their saved step. Finish here only when the Stop gate permits it.

Read before acting:

```text
skills/herdr-foreman/references/round-completion.md
```

For the daily standup, use `Skill(skill: "herdr-standup")`.
