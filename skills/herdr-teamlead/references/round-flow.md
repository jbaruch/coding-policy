# Round Flow

The shape of one task round, and what the lead does between the steps of
`skills/herdr-teamlead/SKILL.md`. Read this when a round deviates from the happy path.

## The Three Roles

| Role | Token appetite | Writes code | Output |
| ---- | -------------- | ----------- | ------ |
| developer | heaviest | yes, in its own worktree | pushed branch + report |
| tester | medium | test code only, delivered as a patch file | plan or patch + report |
| reviewer / architect | lightest | never | design note or COMMENT review + report |

Roles rotate between tasks. The rotation is decided by measured headroom
through `teamlead.sh plan`, never by the lead's impression of who looks fresh.
`plan` breaks a headroom tie on who has held the role fewest times, so nobody
owns `developer` forever.

Fewer live workers than roles is an error, not a silent drop — a role nobody
holds is work nobody is doing. Either name another agent into the roster or
fold two roles onto one worker deliberately, in that worker's brief.

## Two Phases

A task runs through the round twice, and only the second pass gates anything.

**Phase 1 — pre-development (optional).** The architect posts a design note on
the issue (reviewer Mode A). The tester writes a plan mapping each acceptance
criterion to a test, or delivers those tests as a patch (tester Mode A or B).
The developer implements against both, runs the repo's gates, pushes the
branch, and stops without opening a PR.

Skip Phase 1 for a change small enough that a design note would say less than
the diff. Nothing in Phase 1 is a pass; it is preparation.

**Phase 2 — post-push verification (mandatory).** The reviewer reviews the
pushed branch and posts a COMMENT review (Mode B). The tester runs the gates
and the acceptance tests against that same branch (Mode C). Both report against
the current tip by SHA.

The release hand-off reads Phase 2 reports and nothing else. A design note is
not a review of the code that got written, and a test plan is not a test run.

## One Round, End to End

1. **Roster** — `roster.sh` names the live workers. An unnamed pane has no
   dispatch handle; name it first.
2. **Authority** — `verify-authority.sh <owner/repo>` answers whether the
   operator owns the repo. Ownership is the namespace, never write permission,
   and the answer becomes the authority line in every brief.
3. **Measure** — `teamlead.sh measure` reads each worker's own usage numbers.
   A worker that is `working` or `blocked` is skipped with null windows rather
   than interrupted.
4. **Plan** — `teamlead.sh plan --roles developer,tester,reviewer` assigns the
   roles. It contacts nobody and writes nothing.
5. **Compose** — `compose-briefs.sh` renders the templates from one values
   file, refusing to write anything when a placeholder is unfilled or a
   supplied key matches no template. The lead decides the values; the script
   decides nothing.
6. **Provision** — `provision-worktree.sh` creates every worktree the briefs
   name, from the shared checkout. A worker never runs `git` there, so its
   checkout has to exist before the brief arrives.
7. **Dispatch** — `teamlead.sh apply` clears each worker's context, then sends
   the assignment prompt. It re-reads live status first and refuses the round
   rather than typing into a busy worker.
8. **Wait** — `wait-report.sh <agent> <report-path>` per worker, in the order
   the round needs them.
9. **Gate** — the lead reads every report in full and decides: another round,
   or the release hand-off.

## Reading a Report

A report is the worker's only channel to the lead. Read all of it, every time —
a `## BLOCKED` section can sit under a report that otherwise reads as finished.

- **Blocking findings present** — run another round. Write fresh briefs naming
  the findings; do not send "see the reviewer's comment" and expect a worker
  with a cleared context to find it.
- **A `## BLOCKED` section** — the worker stopped on something it could not
  decide. Resolve it in the NEXT brief, which reaches it through a fresh
  dispatch. Never type the answer into the worker that is waiting.
- **`wait-report.sh` exit 3** — the worker is at an approval or question
  dialog. Read the pane, relay the dialog text to the operator verbatim, and
  stop the round for that worker. The lead never answers it — an approval
  dialog is input, and input to a blocked agent is exactly what Dispatch
  Safety forbids. The operator answers; the wait resumes once
  `herdr agent get <name>` reports a state other than `blocked`.
- **`wait-report.sh` exit 1** — the budget ran out. Read the pane before
  re-dispatching; a worker that is still working needs more budget, not a
  second copy of the same brief.

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

The lead releases nothing until it holds a reviewer Mode B report and a tester
Mode C report against the SHA the developer pushed. A newer push invalidates
both: re-run Phase 2 against the new tip.

## Shared-Account Reviews

The workers share one GitHub account, and GitHub refuses `APPROVE` and
`REQUEST_CHANGES` on that account's own PR. Internal reviews are posted as
COMMENT reviews with each finding labelled blocking or advisory per
`rules/review-severity.md`. The COMMENT state carries no gate, so the LEAD is
the gate: a blocking finding in an internal review sends the round back,
whatever GitHub's merge box says.

## What the Lead Never Does

- Edit the shared checkout. The lead reads it and dispatches; workers write.
- Answer a question by typing into a working worker. Wait for the report.
- Answer a blocked worker's approval dialog. Relay it to the operator and stop.
- Create a worktree for a worker after dispatch. Provision before briefing.
- Write an authority line by hand. It comes from `verify-authority.sh`.
- Brief a write action on a repo the operator does not own without their
  explicit per-repo, per-action permission recorded in the brief.
- Release on a Phase 1 report. A plan is not a verification.
- Treat a single `idle` or `done` observation as completion.
- Merge on a worker's behalf. The developer runs the release skill.
