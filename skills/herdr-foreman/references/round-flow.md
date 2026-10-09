# Round Flow

The shape of one task round, and what the foreman does between the steps of
`skills/herdr-foreman/SKILL.md`. Read this when a round deviates from the happy path.

## Compose the Active Team

| Responsibility | Repository writes | Output |
| --- | --- | --- |
| developer | authorized source work in its own worktree | pushed branch and report |
| tester | test code as a report-directory patch | test plan, patch or verification report |
| reviewer | none | independent COMMENT review and report |
| advisor, investigator, architect | none | bounded recommendation, diagnosis or design report |
| release | release operations only | verified release report |
| judge | none | completed dispute or weighing ruling (binding), or a non-binding `insufficient` |

Activate the responsibilities the next task decision needs. Add specialty
requirements through `references/specialists.md`; a profile on the bench needs
no worker or monitoring loop. One worker holds one assignment in a dispatch.
Compatible expertise may share a responsibility. Consultations with the same
canonical responsibility use separate dispatches, each with its own engagement.

The owner planner applies capability and contribution eligibility before
affordable task familiarity and measured headroom. Step 5 in the skill names
the executable contract. Use measured costs to improve calibration; preserve
the pinned judge and the developer's early-fix reservation.

If no eligible worker fits, record the gap and staff or sequence the work.
Never silently drop a required responsibility or let a contributor independently
verify its own work. Specialist advice does not replace required reviewer and
tester passes.

## Two Phases

An implementation task runs through the round twice, and only the second pass
gates release. Investigation-only tasks use Step 12's knowledge-deliverable
gate; they do not require a pushed branch or release reports.

**Phase 1 — pre-development.** An architect or advisor supplies the
needed design or interaction report. An investigator may first resolve a causal
question. Record each consultation's contract lines with `assess-specialist`
before using its outcome. The tester maps each acceptance
criterion to a test, or delivers those tests as a patch (tester Mode A or B).
Plan that work with `--round tester=test_plan`; that dispatch makes its tester
a possible contributor, and no later assessment clears it. A test patch
author is an implementation contributor, even while holding the tester role.
The developer implements against both, runs the repo's gates, pushes the
branch, and stops without opening a PR.

Work that trips a Team Composition trigger gates here: its deliverable lands
before implementation, or the foreman records the staffing decision and its
reason. Step 5's `detect-triggers` run decides which of the four fired, and
reads that recorded decision; the foreman's own reading of the diff does not. The cheap gate is the one worth making mandatory; a reviewer catching
the same thing one finding per round is the expensive one.

Narrow exception to the recorded staffing decision: a legacy-review
classification (`--legacy-review-declaration`, `skills/herdr-foreman/references/specialists.md`)
refuses `--decisions`. Its fired triggers are staffed by role or requirements
specialty, never answered by a decision. Every ordinary and no-write round keeps
the recorded decision.

Untriggered work keeps the old judgement, whatever its size: skip Phase 1 when
a design note would say less than the diff. Nothing in Phase 1 is a pass; it
is preparation.

**Phase 2 — post-push verification (mandatory).** The reviewer reviews the
pushed branch and posts a COMMENT review (Mode B). The tester runs the gates
and the acceptance tests against that same branch (Mode C). Both report against
the current tip by SHA.

The release hand-off reads Phase 2 reports and nothing else. A design note is
not a review of the code that got written, and a test plan is not a test run.

Phase 2 excludes every possible contributor from reviewer and tester. The
owner applies recorded contribution history, which only ever adds an
exclusion; use `--exclude` to narrow the current plan for relevant
contributions outside it, which records no contribution history. A recorded
`none` clears nothing. The reviewer responsibility is verification only; it no
longer carries pre-development Mode A. Historical reviewer responsibility stays
unknown, and unknown excludes.

Consultations can enter later when a new question could change implementation
or verification. Scope any resulting correction through the accepted behavior
and existing correction allowance; a specialist recommendation grants no new
implementation authority. A completed consultation returns to the next needed
assignment, or closes an investigation-only knowledge deliverable through Step 12.

## Four Delivery Stages

`skills/herdr-foreman/references/team-operation.md` Delivery Stages is the one
gate matrix. This flow maps onto it:

1. `authorized` ends after authority, accepted behavior, base, trigger and
   selected-worktree/seat checks permit dispatch.
2. `built` ends when the developer's task-scoped tip is pushed and its declared
   deterministic gates pass.
3. `verified` ends when independent reviewer and tester reports approve that
   exact tip with contributor exclusions intact.
4. `released` ends after hosted gates, merge and every declared publication are
   confirmed.

Maintenance cadence, unrelated checkout observations and advisories stay
visible beside these decisions. They never advance a stage and never block one.
Unknown effect in a stage's own authority, work, verification or publication
evidence fails that stage closed.

## One Round, End to End

1. **Roster** — `roster.sh` names the live workers. An unnamed pane has no
   dispatch handle; name it first.
2. **Authority** — `verify-authority.sh <owner/repo>` answers whether the
   operator owns the repo. Ownership is the namespace, never write permission,
   and the answer becomes the authority line in every brief.
3. **Measure** — `foreman.sh measure` reads each worker's own usage numbers.
   A worker that is `working` or `blocked` is skipped with null windows rather
   than interrupted.
4. **Plan** — `foreman.sh plan` assigns the requested responsibilities with
   specialty requirements and explicit contribution exclusions where needed.
   It contacts nobody; owner state migration may save an older ledger.
5. **Package** — Step 6 builds a range-specific VCS artifact for reviewer and
   tester briefs. Keep the original task base for full reviews and the prior
   reviewed tip for scoped re-checks; a new tip gets a new package.
6. **Compose** — `compose-briefs.sh` renders the templates from one values
   file, refusing to write anything when a placeholder is unfilled or a
   supplied key matches no template. The foreman decides the values; the script
   decides nothing.
7. **Provision** — `provision-worktree.sh` creates every worktree the briefs
   name, from the shared checkout. A worker never runs `git` there, so its
   checkout has to exist before the brief arrives.
8. **Dispatch** — `foreman.sh apply` uses Step 10's explicit context mode,
   then sends the assignment prompt. The recovery reference governs developer
   fix retention and the separate assessed specialist continuation path.
   It re-reads live status and refuses to type into a busy worker.
9. **Observe** — `supervision-watch` observes every enrolled worker. Verify
   candidates with `wait-report.sh --once`, ledger outcomes, and acknowledge
   handled events under `references/supervision.md`.
10. **Gate** — the foreman reads every report in full and gates on its recorded
   contract lines, its classifier gates and the judge's rulings: another
   round, or the release hand-off.

Before relying on a reviewer, tester or consultation report, save its delivery
receipt and run `assess-specialist` under `references/specialists.md`. Record
the outcome its lines support in the task ledger and resolve its supervision
obligations separately. Keep
useful sessions available for likely follow-up, while preserving scoped lessons
outside the session. No idle specialist counts as active work.

The foreman appends decisions throughout this flow to the persistent task ledger,
including before pauses and handoffs. `references/task-ledger.md` separates
dispatch and report observations from assignment acceptance and task completion.
All references to the round log here mean that ledger.

## Report Checkpoint Outcomes

Step 11's `check-member` reads the agent, report, recorded base and send time
from the owner records and runs `wait-report.sh --once` with them
(`skills/herdr-foreman/foreman/members.py`). Its JSON carries the
checkpoint's `exit` and delivery JSON as `wait`:

- `exit` 0 confirms delivery, 1 remains pending, 3 confirms blocked, 4 lacks
  confirmed delivery, 5 proves terminal refusal (record it with
  `record-refusal`), and 6 proves an unavailable model identifier (repair the
  row per `skills/herdr-foreman/references/dispatch-recovery.md` Wait outcomes;
  never `record-refusal`)
- The command itself exits non-zero when the dispatch has no recorded send
  time, or when the wait ran without a verdict (`wait_failed`, carrying the
  wait's own exit, 2 included); resolve the diagnostic stderr names, then run
  it again

Then act on the checkpoint:

- Read delivered reports in full
- Pass the worker's checkout as `--worktree` when it has one
- An exit 1 carries either `reason: checkpoint_pending` or a `stall` object
- A `stall` is classified only when `--worktree` names the checkout
- Act on a stall under `skills/herdr-foreman/references/team-operation.md` Stalled Workers
- Record a stall's obligation through `skills/herdr-foreman/references/attention.md`
- Preserve the blocked/refusal and native-recovery paths in the references below
- Never re-dispatch over uncertainty
- Never resend a refused brief to its provider

```text
skills/herdr-foreman/references/supervision.md
skills/herdr-foreman/references/dispatch-recovery.md
```

Save each reviewer, tester and consultation report's successful delivery
receipt and record its contract lines with `assess-specialist` under
`skills/herdr-foreman/references/specialists.md` before retiring its enrollment. A refusal names
the report's gap; what it saves is the `assess-specialist` contract in
`skills/herdr-foreman/references/dispatch-recovery.md`. Record `needs_work` in Step 12 and
re-dispatch the same responsibility with that gap named. A reviewer or tester
re-dispatch spends no developer fix round.

## Reading a Report

A report is the worker's only channel to the foreman. Read all of it, every time —
a `## BLOCKED` section can sit under a report that otherwise reads as finished.

- **A contract gap** — `assess-specialist` refused the report, naming the
  missing, extra, duplicated or malformed line. Record `needs_work` and send
  the same responsibility a fresh brief naming the gap. A reviewer or tester
  re-dispatch spends no developer fix round. Never read around the gap.
- **A recorded `VERDICT: blocking`** — take Step 12's bounded fix path under
  `skills/herdr-foreman/references/team-operation.md` Fix Loops. Keep a stable task identifier and
  advance its fix counter; changing worker or scope never restarts it.
  Name the findings and prior report in each brief. Re-check the findings
  with scoped briefs, then run full verification before release.
- **A `## BLOCKED` section** — the worker stopped on something it could not
  decide. Resolve it in the NEXT brief, which reaches it through a fresh
  dispatch. Never type the answer into the worker that is waiting.
- **`wait-report.sh` exit 3** — the worker is at an approval or question
  dialog. Read the pane and follow
  `skills/herdr-foreman/references/herdr.md` Runtime Dialogs. Resolve an
  already-authorized action without another operator approval, or follow that
  contract's escalation path. Re-read the target and resume
  the report wait after the dialog clears; never resend its assignment.
- **`wait-report.sh --once` exit 1 with `reason: checkpoint_pending`** —
  delivery remains pending. Record the checkpoint, acknowledge its event with a
  scheduled pending recheck, and resume the fleet watcher. Never send a second
  copy of the brief on a status hint.
- **`wait-report.sh --once` exit 1 carrying a `stall` object** — the report is
  absent, the worker is terminal, and the budget measured from `--since` is
  spent. This is terminal for that wait: schedule no further recheck. Read the
  `stall.class`, record the user-attention obligation, and take the recovery
  `skills/herdr-foreman/references/team-operation.md` Stalled Workers names for that class. Never
  commit the partial work on the strength of the tree building.
- Persist blocked dialogs, missing reports, and required operator decisions in
  the attention queue before presenting them. Keep observing unrelated work.
  A pause or handoff must cover the entire active fleet under the supervision
  reference; a single worker's blocker does not release those obligations.

## Release Gate

For an authorized implementation release, Step 12 requires all five:

1. The developer's report names the branch and the commit SHA it pushed.
2. A broad reviewer **Mode B** report reviews that same SHA and records
   `VERDICT: approved`.
3. A broad tester **Mode C** report verifies that same SHA, with the repo's
   gates run, and records `VERDICT: approved`.
4. Nothing has been pushed to the branch after those two reports.
5. The task carries no open verdict gate (`report-gate-status`). `apply`
   enforces this one itself: it refuses a fresh `release` dispatch, dry runs
   included, while any is open. A completed release replay is exempt; fix,
   re-check and judge dispatches are never held.

A blocking `VERDICT:` from a `security`, `ux-product` or `documentation`
consultation is a blocking finding for the round, taken through the Blocking
Gate and fix loop like a reviewer's or tester's.

A disputed blocking verdict settles one of two ways. A completed adjudication
(`uphold` or `amend`) decides it, and the gated responsibility's next report at
the tip, citing the ruling and recording `VERDICT: approved`, clears its gate.
A finding a weighing ruled `defer` or `decline` is settled the same way: the
next reviewer or tester report at the tip marks it DECLINED, citing the ruling,
and records `VERDICT: approved` (see
`skills/herdr-foreman/references/judge-round.md`). The foreman never matches
rulings to findings, and `report-gate-clear` refuses a judge's report as
evidence for a verdict gate.

Under a recorded `stop` remedy, 2 and 3 read against what ships: the excluded
defect is a tracked accepted defect and the shipped scope carries no other
blocking finding. `rules/review-severity.md` Judge-Accepted Defect Carve-Out
carries the preconditions; every other gate here is unchanged.

A Phase 1 design note or test plan does not satisfy 2 or 3. A report against an
older SHA does not either: re-run Phase 2 against the current tip. After scoped
re-checks close the findings, re-run Phase 2 with `full` briefs before handing off
to release. Scoped reports alone never satisfy this gate.

Reassess both passes against current contribution history before releasing. A
later discovery that a verifier shaped the task's design or implementation
invalidates its independence, even when the reviewed SHA is unchanged. Preserve
the historical receipt, append the new gate decision and its evidence to the
task ledger, and obtain a fresh independent report. Never rewrite the prior
review or treat an old approval as authority over newer contribution evidence.

## Ancestor-Sensitive Fixtures

Some tools resolve their configuration by walking filesystem ancestors, so a
fixture placed under the reports directory is not isolated from the operator's
own project: an isolated `HOME` and the XDG directories are not enough when the
lookup starts at the fixture and climbs. One real rehearsal initialized the
operator's home-level Tessl project that way, and an unauthenticated run then
wrote a rule index missing a private dependency.

The brief names a task-owned fixture root for that work, outside every ancestor
that configures the tool, and the worker keeps every report, plan and patch
artifact under the reports directory as usual
(`skills/herdr-foreman/references/team-operation.md` Writers and Checkouts carries the
preconditions).

The root is this assignment's own: created under a name no other assignment
uses, never a directory that already exists and never one reached through a
symlink. The cleanup at the end removes it, and a reused or linked root would
make that cleanup delete somebody else's files.

Inside that root, before any command that writes through the tool:

1. Resolve the root to its physical path, then walk its ancestors and record
   which of them configure the tool.
2. Pre-seed the intended local manifest so the lookup settles inside the
   fixture rather than above it.
3. Prove the effective root the tool resolved, and compare it to the fixture.
   A root anywhere else stops the rehearsal.
4. Record a digest of each user-level file the rehearsal can reach, and read
   it again afterwards; never copy a directory tree. Before the run, copy only
   the single files the rehearsal is expected to change, and restore each of
   those from its copy afterwards. An unexpected change stops the rehearsal
   and goes to the operator in the report, unrestored; never reinstall the
   operator's environment to recover it.

Which files those are, and which manifest a given tool reads, belong to the
tool's own documentation — not to this reference or a brief.

## Blocking Gate

At Step 12, apply `skills/herdr-foreman/references/assignment-reasoning.md` to
the findings and their proposed corrections. Preserve required judge rulings
and operator decisions; scope classification never waives a blocking finding.
Read this task's confirmed fix history, name the next fix number,
and treat the return to Step 4 as a round boundary: log the round and reset
(SKILL.md Steps 16–17), recording the fix number, findings and prior reports in
the stow, so the reset context returns to Step 4 with self-contained briefs
carrying them. Preserve the developer for retained fixes; use a fresh context for the
fresh-worker stage. Never reset the counter during re-planning. At a contested
verdict, a report `VERDICT:` the classifier gate contradicts, or a weighing
nomination, go to Step 13 first.
Nominations wait for the gate's weighing; other blocking findings take the
fix path meanwhile. At an exhausted allowance,
record the checkpoint through the owner commands in
`skills/herdr-foreman/references/dispatch-recovery.md`, report implementation
as `awaiting_diagnosis`, consult the investigator with round context
`{"investigator": {"diagnosis_input": true}}`, and go to Step 13 with its
assessed report. Use the plan its
remedy records for the bounded extra attempts; collect each preceding attempt's
actual blocking review before continuing.

The owners record the gate itself, never the foreman. `assess-specialist`
(a replay included) and `record-report` write a verdict gate into the report-gate
store whenever the report's parsed `VERDICT:` line is `blocking`, one gate per
report bytes. The gate never refuses the assignment's acceptance: a blocking
report is still an accepted assignment. It holds the task's release (Release
Gate item 5) until an explicit clear, one per blocking report:

- **Re-check** — `report-gate-clear --report <blocking report> --evidence
  <re-check report> --reason <what it settled>`, citing the gated
  responsibility's later report once its `VERDICT: approved` is recorded.
- **Operator decision** — `report-gate-clear --report <blocking report>
  --decision <resolved attention decision>`, as for a classifier gate.
- **Judge ruling** — refused. The ruling decides; the re-check that cites it
  clears.

Exit 0 prints the resolved gates; exit 1 names what the cited evidence lacks
and records nothing. A clear on a report that also carries a classifier gate
resolves both or neither. What qualifies as a re-check is in
`skills/herdr-foreman/references/report-classifier.md` Verdict Gates.

## Branch-Changing Ruling

Acting on a ruling, the foreman never edits the branch itself. At an exhausted allowance,
record the checkpoint through the owner commands in
`skills/herdr-foreman/references/dispatch-recovery.md` and take the diagnosis;
the boundary is a diagnostic question, not a budget prompt. The plan its remedy
records covers multiple attempts within those bounds; fresh release handoffs do
not ask for context-change permission. Report implementation as
`awaiting_diagnosis` while the diagnosis is pending. Record the remedy once and
continue within it.
Otherwise return to Step 12 carrying `ACTION:` verbatim as required work. Count
that implementation as the next fix, under the same task identifier, and gate the
resulting tip again before release.

## Why Phase 2 Runs Before the PR

The registry build's PR #27 went through **12 automated review rounds** —
policy reviewer plus Copilot — because the code reached the bots before the
team's own reviewer and tester had seen it. Each round cost a full CI cycle and
a context reload on the developer.

A plain branch push does not trigger the policy reviewer. That gap is the
opening: the developer pushes the branch and stops. The tester and the reviewer
run against the **pushed branch**, the developer folds their blocking findings
in, and only then does the PR open — so the bots review work the team has
already agreed on, and the usual outcome is one round instead of twelve.

The hand-off is therefore split around the bots:

- Developer runs `Skill(skill: "release")` **Steps 1–4** — readiness, PR,
  version reasoning, and the review request.
- The bots review once.
- Developer runs **Steps 5–7** — watch the reviews, act on blocking findings,
  merge and clean up.

The foreman releases nothing until it holds a reviewer Mode B report and a tester
Mode C report against the SHA the developer pushed. A newer push invalidates
both: re-run Phase 2 against the new tip.

## Shared-Account Reviews

The workers share one GitHub account, and GitHub refuses `APPROVE` and
`REQUEST_CHANGES` on that account's own PR. Internal reviews are posted as
COMMENT reviews with each finding labelled blocking or advisory per
`rules/review-severity.md`, and its report records the one `VERDICT:` line.
The COMMENT state carries no gate, so the FOREMAN is the gate: a recorded
`VERDICT: blocking` sends the round back, whatever GitHub's merge box says.

## The Judge

A reserved seat outside ordinary staffing, on the most capable model
available. It holds no other responsibility. `skills/herdr-foreman/references/team-operation.md`
Judge Seat is the contract; this section is the operational detail for
`skills/herdr-foreman/SKILL.md` Step 13, whose seven steps run from
`skills/herdr-foreman/references/judge-round.md`.

It runs in two modes. Adjudication settles a dispute or weighs findings;
diagnosis asks why a fix loop is not converging. Dispatch adjudication on
exactly one of four triggers:

- A contested reviewer or tester verdict — one worker's finding, another
  worker's disagreement, neither side able to settle it by re-reading the rule.
- A report `VERDICT:` the classifier gate contradicts — a classifier `block`
  gate on a report recording `VERDICT: approved`. The foreman settles that
  contradiction by neither the line nor the label.
- A bot finding the team disagrees with — the policy reviewer or Copilot flags
  something the developer and reviewer both think is wrong.
- A weighing nomination — a blocking finding `foreman finding-churn` places on
  lines the previous fix round added, or one a worker report marks `MARGINAL:`.
  The weighing asks whether the fix is worth its cost, never whether the
  finding is real, and the judge's report is the ruling file
  `skills/release/dismiss-ruled-review.sh` reads.

A dispute is settled once, and a finding is weighed once. Dispatching an
adjudication at every allowance boundary made the seat a per-round toll on the
window its developer and reviewers already share; that trigger is gone and
stays gone.

An exhausted allowance is a different question, and it gets the second mode —
after the investigator. That profile is written for "unclear causality or
repeated unsuccessful fixes", it is read-only and bounded, and it is the
cheaper seat: it produces the reproduction and causal assessment, and the
judge rules on them. A diagnosis without one is refused.
The loop that exhausts its budget is rarely short of attempts: a find-rate
that holds flat while every round closes its finding is a structural problem,
and more rounds reproduce it. Diagnosis asks why the loop is not converging
and what has to change. The foreman ran the loop and is the wrong diagnostician
of its own dispatch pattern, so the read is independent for the same reason a
review is.

Its report opens with six lines rather than three:

```
DIAGNOSIS: <why the loop is not converging, from the evidence>
REMEDY: continue — <rounds, approach unchanged> | restructure — <the change> | stop — <what ships, what is tracked>
BOUND: <developer attempts this remedy allows> — <why that number> | none — for stop
ASSESSMENT: <the investigator report this diagnosis ruled on>
EVIDENCE: <the rounds, findings and diffs the diagnosis rests on>
UNVERIFIED: <anything unconfirmed against the tree, or "none">
```

Two further lines approve a materially different direction:

```
APPROACH: <the direction that replaces the failed one>
VERIFICATION: <what confirming this direction looks like>
```

The allowance bounds repeated attempts at an approach already shown to fail, so
a different direction starts its own allowance and its own ladder while the
task's cumulative attempt numbering continues unchanged. `BOUND` then names the
new approach's allowance and no correction plan is recorded. The cited
investigator report carries `FAILED APPROACH`, `ROOT CAUSE` and `EXPERIMENT`. A
new worker, a cleared context, a rewritten brief and a repeated remedy label
leave the approach unchanged. A direction already tried is refused, and `stop`
approves no direction at all.

A `continue` or `restructure` remedy carries the attempt budget in `BOUND`,
which is the number the operator used to supply. It is counted in developer
attempts, justified against the cited evidence, and capped: the recording
command refuses a bound above its ceiling rather than honouring it. `ASSESSMENT`
names the investigator report the judge ruled on, and the record binds that
path the way supervision's enrollment binds the judge's own report. A `stop`
remedy ships what is clean and records the remainder as a tracked accepted
defect; that authority is the judge's, stated so a foreman does not re-escalate
out of caution. Recording it also files a user-attention obligation, so the
operator learns of the override they hold without going to look for it.

The diagnosis is re-enterable when its own remedy's bound exhausts with
blocking work remaining. A remedy that was independently diagnosed and still
did not work is evidence for the next diagnosis, not for the operator, who
holds nothing on the second pass they did not hold on the first. Re-entry
moves down the ladder `continue` → `restructure` → `stop`, or repeats one rung
once against a recorded `PROGRESS` line: a remedy that produced nothing is
never reissued, the ladder never runs backwards, a rung already repeated is
spent, and `stop` is terminal, which bounds an approach's diagnoses (see
`skills/herdr-foreman/foreman/recovery.py` `_remedy_options`). No
operator sits in the path of any of them. The ladder is read per approach, so
an approved new direction starts at `continue` rather than inheriting the rungs
the approaches it replaced spent.

`skills/herdr-foreman/references/team-operation.md` Judge Seat carries the contract; the record
shapes are the owner's, in `references/dispatch-recovery.md`.

It is read-only without exception in either mode: no file edit, no mutating
git or `gh` command, no GitHub post, no subagent dispatch.

Adjudicating, it reads both positions and the governing rule, checks only the
evidence each position cites, and the citations of any attached investigator
report, against the tree rather than trusting either
side's framing, and never explores beyond those citations. It returns a report
opening with three
lines — `RULING: uphold A | uphold B | amend — <line> | insufficient — <facts needed> | blocked — <question>`,
`ACTION:` naming the minimal step, `UNVERIFIED:` naming anything it could not
check — followed by its numbered reasons.

Those three lines belong to adjudication; a diagnosis carries the five above
and never a `RULING:` or an `ACTION:`.

`insufficient` is the judge declining to rule because the cited evidence
cannot settle a named fact. It settles nothing, binds nothing, and no
checkpoint cites it; an investigator establishes the fact and the judge rules
again with that report. An attached investigator report's citations are
admissible evidence, on the first dispatch or after `insufficient`.

`blocked` is the judge declining to rule on a question only the operator can
answer: authority, intent, or a choice no tree records. The round stops there
and the named question goes to the operator. The foreman does not dispatch a second judge and does not
rule in its place.

A completed ruling or remedy binds the round the moment the foreman reads it. Only the
operator overrides one; record the override and why in the round log. No
diagnosis remedy waits on an operator for the task to reach a terminal state.
A `blocked` adjudication is the one ruling that does: it stops the round and
sends its named question to the operator, as it always has.

The judge worker kind is declared in the main `config.json` and is measured and
planned like every other seat, but its seat is pinned rather than ranked: the
`judge` block names the kind, model and effort, and the planner creates a fresh
assignment identity on that kind.

Kinds on the same subscription declare the same `window_group`; measurement
uses one disposable probe for the group and the planner charges a seat's cost
against every kind sharing that window. When that window is exhausted the round
halts: there is no substitute judge, no fallback to another vendor's flagship,
and no degraded ruling.

## What the Foreman Never Does

- Edit the shared checkout. The foreman reads it and dispatches; workers write.
- Answer a question by typing into a working worker. Wait for the report.
- Grant authority beyond the task when resolving a runtime dialog.
- Create a worktree for a worker after dispatch. Provision before briefing.
- Write an authority line by hand. It comes from `verify-authority.sh`.
- Brief a write action on a repo the operator does not own without their
  explicit per-repo, per-action permission recorded in the brief.
- Release on a Phase 1 report. A plan is not a verification.
- Treat Herdr status as assignment acceptance or task completion.
- Merge on a worker's behalf. The developer runs the release skill.
- Act against a judge's ruling, or seat the judge on developer, reviewer, or
  tester. Only the operator overrides a ruling.

## Dispatch Results

Step 10 of `skills/herdr-foreman/SKILL.md` follows these outcomes. Before a
finish, apply the whole-fleet pause/handoff contract in `references/supervision.md`.

- **Exit 0** — proceed to Step 11.
- **Busy target** — no dispatch occurred. Wait for readiness or replan; stay
  at this step.
- **Sent but not started** — inspect the pane; never re-dispatch on top of the
  message. Proceed to Step 11 for the roles that started.
- **Retrospective, clear, composer, tier, or continuity refusal** — follow the
  diagnostic and recorded dispatch outcome. Reconcile uncertainty before retrying;
  wait for roles whose records confirm dispatch.
- **Unknown refusal** — report it verbatim and finish here.
- **`--dry-run`** — inspect the context choice, requested tier, and relaunch
  argv. It contacts no worker, writes no ledger, and proves no live tier.
  Finish here.


## Ruling Outcomes

`skills/herdr-foreman/references/judge-round.md` step 7 follows these branches. Before a finish, preserve any user question and
apply the whole-fleet pause/handoff contract in `references/supervision.md`.

A completed `RULING:` line binds the round. Only the operator overrides it. `insufficient` binds nothing.

For an investigation-only task, a completed ruling (`uphold A`, `uphold B` or
`amend`) returns to Step 12's
knowledge-deliverable gate with the ruling and any required authorized research.
Apply the existing correction allowance and judge rules to remaining findings.
Do not enter implementation Phase 2 or Step 14 without implementation/release
authorization. A blocked ruling follows the operator-question path below.

- **`insufficient`** — the cited evidence cannot settle the named facts.
  Dispatch an investigator under
  `skills/herdr-foreman/references/specialists.md` to establish
  exactly those facts with citations. Then re-dispatch the judge on the same
  dispute, filling `INVESTIGATION_REPORT` with that report. The dispute is not
  settled, so this is not a second ruling on a settled dispute.
- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing no branch content**
  — record the ruling. Proceed to Step 14 only with Step 12's broad reports
  against the current tip. Otherwise re-run Phase 2 with full briefs carrying
  the ruling. Do not re-dispatch the judge for the same settled dispute. The
  ruling clears no verdict gate: the re-check that cites it and records
  `VERDICT: approved` does, through the Blocking Gate's `report-gate-clear`.
- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing the branch** — apply
  the round-flow reference's Branch-Changing Ruling contract. Finish here while
  its required operator decision is pending; otherwise return to Step 12 with
  `ACTION:` as the next counted fix.
- **`RULING: weighed`** — first run `foreman verify-ruling` on the delivered
  report (judge-round step 7). A failed verification applies nothing: the
  nominated findings stay blocking. Once it passes, record the ruling. Its `fix` findings return to
  Step 12 as the next counted fix, `ACTION:` carrying them. Its `defer` and
  `decline` findings stop blocking while the ruling covers them (see
  `skills/herdr-foreman/references/judge-round.md`), and the release brief
  carries the ruling as `WEIGHING_RULING`. Record an `update` attention item naming the ruling; it
  gates nothing. Never nominate a covered finding again.
- **`blocked`** — the judge declined to rule. Stop the round and put its
  named question to the operator. Do not dispatch a second judge and do not
  rule in its place. Finish here.
- **`REMEDY: continue`** — record the diagnosis, then return to Step 12 and
  spend the bounded rounds with the approach unchanged.
- **`REMEDY: restructure`** — record the diagnosis, apply the named structural
  change to the shape of the work, then return to Step 12 within its bound.
  The change is the judge's to name and the foreman's to carry out.
- **`REMEDY: stop`** — record the diagnosis, release what is clean, and record
  the remainder as a tracked accepted defect. The remedy carries that
  authority; do not re-escalate it. Proceed to Step 14 for what ships, once
  the reviewer and tester passes on the shipped tip have cleared its verdict
  gates as re-checks. The
  operator overrides it with a plan over the remedy or a different approach
  through `authorize-approach`, and the diagnosis path reopens with that
  approach's own ladder.
- A remedy's bound exhausting with blocking work remaining returns to Step 13
  for the next diagnosis, one rung down the ladder — or at the same rung once,
  when the spent remedy made progress the new diagnosis records in `PROGRESS`.
  Never re-enter above the last rung, and never repeat a rung twice.
