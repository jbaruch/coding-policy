---
name: herdr-teamlead
description: >
  Run a team round across three Herdr worker panes: assign developer, reviewer,
  and tester by subscription headroom and qualified model tiers, compose briefs,
  provision worktrees, dispatch workers, collect reports, gate release, and ask
  a pinned judge to resolve disputes. Use for requests to dispatch the Herdr
  team, balance worker usage, or collect team reports. Requires HERDR_ENV;
  standalone edits, reviews, and investigations do not use this skill.
---

# Herdr Team Lead Skill

Process steps in order. Do not skip ahead.

Lead three rotating workers and a pinned judge. Assign roles, compose briefs,
read reports, and gate the round. Never edit the shared checkout or implement
for a worker. Optional Phase 1 produces a design/test plan and implementation;
mandatory Phase 2 reviews and verifies the pushed tip before release.

Open detailed contracts as needed:

```text
skills/herdr-teamlead/references/herdr.md
skills/herdr-teamlead/references/round-flow.md
skills/herdr-teamlead/references/round-setup.md
skills/herdr-teamlead/state-schema.md
```

## Step 1 — Determine the Mode

Read `HERDR_ENV` before running any script.

- **Unset or empty** — this skill does not apply. Say so and do the task
  directly, without roster calls, briefs, provisioning, reports, or simulated
  worker roles. Finish here.
- **Set, with a team task** — proceed immediately to Step 2.
- **Set, with a single edit, question, lookup, or existing-code review** — do
  it directly. Finish here.

## Step 2 — Verify Herdr and the Roster

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/roster.sh
```

Emits caller pane and named live agents with kind, pane, and state.

- **Exit 0, agents present** — proceed to Step 3.
- **Exit 0, empty roster** — report unnamed panes from `herdr agent list` and
  the correcting `herdr agent rename <pane-id> <name>` command. Finish here.
- **Exit 1 or 2** — report the diagnostic verbatim and finish here.

If workers cannot cover the roles, name another worker or deliberately combine
roles within one brief. Record that decision; never duplicate a dispatch target.

## Step 3 — Verify Authority for the Repo

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/verify-authority.sh <owner/repo>
```

Emits repo, viewer/owner identities, permission, namespace ownership, and
`authorized`. Authorization reflects namespace ownership alone.

- **`authorized: true`** — set `AUTHORITY_STATEMENT` to `owner of <owner/repo>`
  and `EXTERNAL_PERMISSION` to `none`. Proceed to Step 4.
- **`authorized: false`** — use explicit operator permission naming this repo
  and each write action. Record their words in `EXTERNAL_PERMISSION` and set
  authority to `not owner; permitted this round: <their words>`. Without that
  permission, proceed read-only or finish here.
- **Exit 1 or 2** — report the diagnostic verbatim and finish here.

Proceed immediately to Step 4 with the recorded authority.

## Step 4 — Measure Headroom

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh measure
```

Emits and saves a snapshot with per-agent headroom, windows, state evidence,
`tier_billing`, and `failed_agents`. Busy workers are skipped. Unmeasured tier
billing stays `unknown`. Report each failed measurement and obtain that
worker's reading before relying on its seat.

Use `--trace` for unexplained transport behavior. Usage parsing, polling knobs,
and trace redaction are documented in:

```text
skills/herdr-teamlead/references/round-setup.md
skills/herdr-teamlead/teamlead/measure.py
```

Proceed immediately to Step 5 once the required readings are available.

## Step 5 — Plan the Roles

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh plan \
  --roles developer,tester,reviewer \
  [--exclude <role>=<agent>[,<agent>...]]... \
  [--round <role>=<round-type>] [--round-context <evidence.json>] [--fix-round <N>]
```

Emits assignments, rationale, snapshot reference, and configured round tiers;
contacts no worker. Exit 1 refuses the plan: resolve its diagnostic before
continuing. Phase 2 excludes the branch author from reviewer and tester.
For retained fixes, plan developer alone and exclude all other workers; plan
verification separately. Supply the same fix number to plan and apply.

The operator's table controls model, effort, and launch options. Default
planning excludes unqualified tiers. `--preview-tiers` inspects an
uncommissioned table; live dispatch still requires qualification. Weights,
round inputs, promotion, and metering contracts:

```text
skills/herdr-teamlead/teamlead/planner.py
skills/herdr-teamlead/references/model-tiers.md
```

Save the plan and announce its rationale. Proceed immediately to Step 6.

## Step 6 — Build the Review Package

For reviewer/tester briefs, run from a checkout holding the recorded commits:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/review-package.sh \
  <recorded-base-sha> <pushed-head-sha> <round-reports-dir>/review-<base7>..<head7>.diff
```

Record the task base before initial development and preserve it through fixes.
Full reviews use that base and the current pushed tip; scoped rechecks use the
previous reviewed tip. Never infer the base from `HEAD~1`. Set `REVIEW_BASE`
and `REVIEW_HEAD` to full SHAs and rebuild whenever the range changes.
Pre-development packages use the recorded base at both endpoints and never
prove an implementation. Other roles need no package; proceed to Step 7.

Success prints the absolute artifact path containing range, commits, stat, and
patch. Set it as `REVIEW_PACKAGE`. Any non-zero exit requires fixing the named
input/tool/output failure and retrying; never compose verification briefs
without a completed package. Existing different content is preserved.
Proceed immediately to Step 7.

## Step 7 — Compose the Briefs

Write `{"shared": {...}, "roles": {"<role>": {...}}}` and run:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/compose-briefs.sh \
  .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/templates \
  <values.json> <round-reports-dir>
```

Emits the common file and role-brief paths. Non-zero means invalid input or a
tool/write failure: fix the diagnostic and retry; never dispatch failed
composition. The script validates placeholders, supplied keys, report paths,
and reviewer/tester package paths and full commit IDs before writing.

Supply shared checkout, Step 3's authority/permission, and each role's issue,
branch, worktree, report paths, phase, and mode. Reviewer/tester inputs also
carry Step 6's package and range. Placeholder details and phase/mode table:

```text
skills/herdr-teamlead/references/round-setup.md
```

Briefs identify every issue, finding, file, and prior report in full. Phase 2
names the pushed SHA. Fixes carry prior attempt count and required ownership
handoff. Verification names `full` or `scoped`, prior findings, and the
follow-up for new advisories. Final release verification is `full`.
Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

Run once per writing worker and every worktree named in a brief:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/provision-worktree.sh \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, base, and `created|attached|already-provisioned`. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only Phase 1 reviewers need none. Clean up after merge per
`rules/agent-worktree-isolation.md`. Proceed immediately to Step 9.

## Step 9 — Label the Layout

Optional, once per team; skip an already named sidebar.

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/label-workspaces.sh \
  <lead-label> [<agent>=<workspace-id>]...
```

Emits per-target `renamed|unchanged|failed`; exit 3 names partial failures.
Report label failures and continue. Proceed immediately to Step 10.

## Step 10 — Dispatch the Briefs

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --no-clear]
```

Hands each worker its brief using the selected context mode. Emits one JSON
object: per role a record carrying `cleared`, `clear_reason`, `context_session`, `task`,
`fix_round`, `tier`, `landed`, `started`, and `status`. The tier record carries
the requested pair, verified argv, and evidence source.

Keep the same `--task` identifier from initial development through all its
fixes. Omit `--fix-round` on the initial assignment; supply it on every fix.
Leading or trailing whitespace in a task label is refused; existing ledger
identities are never trimmed or merged. A padded legacy identity requires an
explicit operator recovery decision; retrying the padded label cannot succeed.
Dispatch a retained fix as a developer-only assignment with
`--retain-context`. Other roles receive separate, cleared assignments.
The utility refuses an invalid mode or an exhausted fix counter before any
Herdr operation. Live fixes must advance the task's confirmed history.
Retention also requires the same worker's matching prior assignment and live
native session identity from Herdr's official integration. A missing or changed
identity refuses retention before any terminal write. Check
`herdr integration status` when the identity is unavailable; never infer
continuity from the worker's name, readiness, or pane text.
Missing or migrated history cannot authorize it. A retained worker keeps a
verified compatible model and effort; retention never relaunches a worker.
Tiered live dispatch also requires the recorded validation battery and current
canary. A missing qualification refuses before any worker operation. If context is unavailable,
stop the retained path and report the loss; do not reset the fix counter.

Only dispatched workers can produce reports. Wait on the roles that landed.

- **Exit 0** — proceed to Step 11.
- **Busy target** — no dispatch occurred. Wait for readiness or replan; stay
  at this step.
- **Sent but not started** — inspect the pane; never re-dispatch on top of the
  message. Proceed to Step 11 for the roles that started.
- **Clear, composer, tier, qualification, or continuity refusal** — no brief
  reached that role. Follow the named recovery before retrying it; wait only
  for other roles whose records show a dispatch.
- **Unknown refusal** — report it verbatim and finish here.
- **`--dry-run`** — inspect the context choice, requested tier, and relaunch
  argv. It contacts no worker, writes no ledger, and proves no live tier or
  qualification. Finish here.

Recovery and executable refusal contracts:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
skills/herdr-teamlead/teamlead/assign.py
skills/herdr-teamlead/teamlead/launch.py
```

Proceed to Step 11 with the dispatched roles.

## Step 11 — Wait for the Reports

One call per dispatched worker, in the order the round needs them:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/wait-report.sh <agent-name> <report-path>
```

Emits `{"agent","state","report_path","found","elapsed_seconds"}` on every
outcome except exit 2, which leaves stdout empty and puts its diagnostic on
stderr. Exit 4 adds `reason`. Completion requires both the report file on disk and the `REPORT: `
marker in the worker's pane. A single `idle` or `done` observation is not
completion. Poll interval and give-up budget are the script's own constants.

- **Exit 0** — read the completed report; continue to the next worker, then Step 12.
- **Exit 1** — inspect the named worker. Re-run this wait if it is working;
  otherwise record the missing report and continue to the next worker.
- **Exit 2** — report the tool failure and finish here.
- **Exit 3** — relay the blocked worker's dialog to the operator and stop its
  round. Resume the wait only after the live state leaves `blocked`.
- **Exit 4** — the file lacks its confirmed delivery marker. Follow the live
  state check in the recovery reference; never re-dispatch on top of it.

Detailed recovery for each outcome:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
```

Proceed to Step 12 once each dispatched worker has a completed or recorded
missing report.

## Step 12 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.

- **Any blocking finding** — follow `rules/agent-team-operation.md` Fix Loops.
  Read this task's confirmed fix history, name the next fix number, and return
  to Step 4 with self-contained briefs carrying the findings and prior
  reports. Preserve the developer for retained fixes; use a fresh context
  for the fresh-worker stage. Never reset the counter during re-planning.
  At the cap, a contested verdict, or a lead override, go to Step 13 first.
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

With all four met, proceed immediately to Step 13.

## Step 13 — Compose the Judge Brief

Optional. Triggers and the ruling contract are in
`skills/herdr-teamlead/references/round-flow.md` "The Judge" (a bot
disagreement inside Step 20 returns here first). No trigger — proceed to
Step 20.

Compose the brief from `templates/brief-judge.md` through Step 7: the
dispute, both positions with report paths, the governing rule, the tree. Skip
Step 8 for the read-only judge. Proceed immediately to Step 14.

## Step 14 — Re-measure the Shared Window

Step 4's snapshot is a hint, not authority (`rules/stateful-artifacts.md`).
The judge's affordability is decided from a fresh reading, never from it.

Re-run Step 4's `measure`. That step's outcomes govern this run unchanged. A
`measure` that cannot read the judge worker's window is a stale-state failure,
resolved there before a ruling is planned.

Hand the fresh snapshot to Step 15 and plan nothing here. Proceed immediately
to Step 15.

## Step 15 — Plan the Judge Seat

Run Step 5's `plan` with `--roles judge`, against the Step 14 snapshot. It
names the worker the `judge` block pins, and refuses the round when that
window cannot cover a ruling:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh plan \
  --roles judge --snapshot <step-14-measure-output>
```

- **Exit 0** — the plan file names the judge worker. Proceed to Step 16.
- **Non-zero naming the judge's headroom** — the window cannot cover a
  ruling. Report it and finish here. There is no substitute judge, no
  fallback to another model, and no degraded ruling.
- **Any other non-zero** — a malformed config, a pinned worker absent from
  the snapshot, an unreadable snapshot, or a plan nobody can fill. Report the
  diagnostic verbatim and finish here. Do not hand-write an assignment to
  work around it.

The plan's `judge` object carries the seat's tier — `agent`, `model` and
`effort`, straight from the `judge` block. Step 16 launches the worker from
those values. Proceed immediately to Step 16.

## Step 16 — Start the Judge Worker on Its Pinned Tier

Run:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/start-judge-worker.sh \
  <step-15-plan-file> <pane> [claude|codex|grok]
```

It reads the tier from the plan's `judge` object, starts the worker on it,
and verifies the returned launch argv. Its input, output, and failure
contracts are in the script header. Pane text never proves the tier.

- **Exit 0** — the launch argv proved the tier. Its JSON names the agent, model
  and effort. Proceed immediately to Step 17.
- **Any non-zero** — report the script's diagnostic verbatim and finish here.
  A worker whose tier is unproven does not get briefed, and no tier is set by
  hand to work around it.

## Step 17 — Dispatch the Judge

Dispatch under Step 10's outcome contract exactly, passing the plan file from
Step 15 rather than a hand-written mapping:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief judge=<round>-judge.md \
  --common <path-to-COMMON.md> \
  --task <round> --no-clear
```

Step 10's outcomes govern this dispatch. `--no-clear` preserves the worker
started in Step 16; apply verifies its live process arguments before sending
the brief. Proceed immediately to Step 18.

## Step 18 — Wait for the Ruling

Wait with Step 11's `wait-report.sh <agent> <report-path>`, where `<agent>` is
the worker the Step 15 plan named. Proceed immediately to Step 19 once the
report lands.

## Step 19 — Act on the Ruling

The `RULING:` line binds the round. Only the operator overrides it.

- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing no branch content**
  — record the ruling. Proceed to Step 20 only with Step 12's broad reports
  against the current tip. Otherwise re-run Phase 2 with full briefs carrying
  the ruling. Do not re-dispatch the judge for the same settled dispute.
- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing the branch** — the
  lead never edits the branch itself. At an exhausted fix cap, report BLOCKED
  with the ruling and proposed plan to the operator; finish here. Otherwise
  return to Step 12 carrying `ACTION:` verbatim as required work. Count that
  implementation as the next fix, under the same task identifier, and gate
  the resulting tip again before release.
- **`blocked`** — the judge declined to rule. Stop the round and put its
  named question to the operator. Do not dispatch a second judge and do not
  rule in its place. Finish here.

## Step 20 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 7 with the role `release` for
the developer's agent (template `templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 8 (it reports
`already-provisioned`), dispatch through Step 10 so the context is cleared and
the brief is fresh, and wait on the report in Step 11. The worker merges. You
do not. Proceed immediately to Step 21 after its report.

## Step 21 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`. Proceed immediately to Step 22.

## Step 22 — Log the Round

Log the round: the assignments, the report paths, the findings, and the
outcome. If the round produced no findings at all, log it and say so in one
line rather than reproducing the reports. Finish here.

For the daily standup, which is a different round shape entirely, use
`Skill(skill: "herdr-standup")`.
