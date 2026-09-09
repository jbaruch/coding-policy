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

Follow `rules/agent-team-operation.md` for round constraints.

Each command block resolves `CP` to the project-local plugin, falling back to
`$HOME/.tessl/plugins/jbaruch/coding-policy`. Run the resolver in every call.
Prose `skills/...` paths are relative to that plugin root.

References:

```text
skills/herdr-teamlead/references/herdr.md
skills/herdr-teamlead/references/round-flow.md
skills/herdr-teamlead/references/round-setup.md
skills/herdr-teamlead/references/task-ledger.md
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
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Emits the caller and live workers with kind, pane, and state.

- **Exit 0, agents present** — proceed to Step 3.
- **Exit 0, empty roster** — report unnamed panes from `herdr agent list` and
  the correcting `herdr agent rename <pane-id> <name>` command. Finish here.
- **Exit 1 or 2** — report the diagnostic verbatim and finish here.

If roles lack workers, name one or record combined roles in a single brief.
Never duplicate dispatch targets.
Start workers in YOLO mode under `references/model-tiers.md`; preserve it on
relaunch. Verify live permission flags before dispatch, including existing workers.

## Step 3 — Verify Authority for the Repo

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/verify-authority.sh" <owner/repo>
```

Emits ownership evidence; `authorized` reflects namespace ownership alone.

- **`authorized: true`** — record `owner of <owner/repo>` in
  `AUTHORITY_STATEMENT`; set additional `EXTERNAL_PERMISSION` to `none`.
- **`authorized: false`** — record `not owner of <owner/repo>`. Reuse explicit
  operator permission for this repo and each write action in
  `EXTERNAL_PERMISSION`; absent permission, proceed read-only or finish here.
- **Exit 1 or 2** — report the diagnostic verbatim and finish here.

Record the task's existing source and words in `TASK_AUTHORIZATION`,
and permitted actions and repo in `AUTHORIZED_ACTIONS`. Read-only uses `none`.
Ownership never expands task scope. Examples: `references/round-setup.md`.
Create or resume the stable task ledger under `references/task-ledger.md`.
Record its absolute path with the task authorization before the first dispatch.
Proceed immediately to Step 4.

## Step 4 — Measure Headroom

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

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles developer,tester,reviewer \
  [--exclude <role>=<agent>[,<agent>...]]... \
  [--round <role>=<round-type>] [--round-context <evidence.json>] \
  --task <task-id> [--fix-round <N>] [--correction-plan <id> --work <work.json>]
```

Emits assignments, rationale, snapshot reference, and configured round tiers;
contacts no worker. Exit 1 refuses the plan: resolve its diagnostic before
continuing. Phase 2 excludes the branch author from reviewer and tester.
For retained fixes, plan developer alone and exclude all other workers; plan
verification separately. Reserve the developer until initial and early-fix verification
resolves before reusing it for another task or role. Supply the same fix number to plan and apply.
Use the same recorded task, approval, and work bounds for both commands.
Register the original task and base through the owner commands documented in
`skills/herdr-teamlead/references/dispatch-recovery.md` before recovery work.

The operator controls tiers and qualification. `--preview-tiers` never
authorizes dispatch.

```text
skills/herdr-teamlead/references/model-tiers.md
```

Save the plan with its rationale. Proceed immediately to Step 6.

## Step 6 — Build the Review Package

For reviewer/tester briefs, run from a checkout holding the recorded commits:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/review-package.sh" \
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

Resolve policy paths through the Step 7 reference first. Write its outputs in
`shared` within `{"shared": {...}, "roles": {"<role>": {...}}}` and run:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/compose-briefs.sh" \
  "$CP/skills/herdr-teamlead/templates" \
  <values.json> <round-reports-dir>
```

Emits the common file and role-brief paths. Non-zero means invalid input or a
tool/write failure: fix the diagnostic and retry; never dispatch failed
composition. The script validates placeholders, supplied keys, report paths,
and reviewer/tester package paths and full commit IDs before writing.
Give every assignment a fresh absolute report path; never reuse a prior
attempt's path or share one between roles.

Supply shared checkout, Step 3's authority/permission, and each role's issue,
branch, worktree, report paths, phase, and mode. Reviewer/tester inputs also
carry Step 6's package and range. Placeholder details and phase/mode table:

```text
skills/herdr-teamlead/references/round-setup.md
```

Apply the Step 7 reference's brief-completeness requirements.
Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

Run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, base, and `created|attached|already-provisioned`. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only Phase 1 reviewers need none. Clean up after merge per
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

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

Emits per-role JSON with clear/session evidence, task, fix number, verified
tier, and delivery status. Labelled dispatches carry `dispatch_id` and any
`context_transition`; fields are documented in `skills/herdr-teamlead/state-schema.md`.
Classify every brief against Step 3's authorization before sending it.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the recovery reference's Dispatch context requirements before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone; other roles clear separately. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

- **Exit 0** — proceed to Step 11.
- **Busy target** — no dispatch occurred. Wait for readiness or replan; stay
  at this step.
- **Sent but not started** — inspect the pane; never re-dispatch on top of the
  message. Proceed to Step 11 for the roles that started.
- **Clear, composer, tier, qualification, or continuity refusal** — follow the
  diagnostic and recorded dispatch outcome. Reconcile uncertainty before retrying;
  wait for roles whose records confirm dispatch.
- **Unknown refusal** — report it verbatim and finish here.
- **`--dry-run`** — inspect the context choice, requested tier, and relaunch
  argv. It contacts no worker, writes no ledger, and proves no live tier or
  qualification. Finish here.

Read the context, recovery, and executable refusal contracts:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
skills/herdr-teamlead/references/model-tiers.md
```

Proceed to Step 11 with the dispatched roles.

## Step 11 — Wait for the Reports

Run for each dispatched worker in the required order:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/wait-report.sh" <agent-name> <report-path>
```

Emits `{"agent","state","report_path","found","elapsed_seconds"}`; exit 2
emits only stderr. Exits 4–5 add `reason`. Delivery requires the file and its
complete, unquoted `REPORT: <absolute-path>` marker on one pane row. Known native
decoration requires completed source-message proof. Names,
quoted examples, wrapped fragments, or lifecycle state alone never confirm it.
The script owns timing. Append every wait outcome to the task ledger before
moving to another worker; keep Herdr state separate from the lead's assessment.

- **Exit 0** — read the report and record delivery; continue to the next worker,
  then Step 12. Delivery alone does not accept the work.
- **Exit 1** — inspect the named worker's live pane and native evidence. Re-run
  this wait for confirmed ongoing work; otherwise record the missing report
  and continue to the next worker. A Herdr label alone decides neither outcome.
- **Exit 2** — report the tool failure and finish here.
- **Exit 3** — relay the blocked worker's dialog to the operator and stop its
  round. Resume the wait only after the live state leaves `blocked`.
- **Exit 4** — the file lacks its confirmed delivery marker. Follow the live
  state check in the recovery reference; never re-dispatch on top of it.
- **Exit 5** — record the report as unavailable and notify the operator.
  Keep review/release gates unsatisfied. Never automatically retry, rephrase,
  switch providers/models, or synthesize a report.

Outcome recovery:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
```

Proceed to Step 12 once each dispatched worker has a completed or recorded
missing report.

## Step 12 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.
Record assignment acceptance or outstanding work in the task ledger against
the inspected report and artifact evidence. Record the task's gate decision
separately; a worker finishing its brief never completes the whole task.

- **Any blocking finding** — follow `rules/agent-team-operation.md` Fix Loops.
  Read this task's confirmed fix history, name the next fix number, and return
  to Step 4 with self-contained briefs carrying the findings and prior
  reports. Preserve the developer for retained fixes; use a fresh context
  for the fresh-worker stage. Never reset the counter during re-planning.
  At an exhausted allowance, a contested verdict, or a lead override, go to
  Step 13 first. Use the recorded bounded plan for authorized extra attempts;
  collect each preceding attempt's actual blocking review before continuing.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

Release requires all four:

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

Re-run Step 4's `measure` under its outcome contract. Resolve any unreadable
judge window before planning. Pass the fresh snapshot to Step 15; never reuse
the earlier reading as affordability proof. Proceed immediately to Step 15.

## Step 15 — Plan the Judge Seat

Plan the pinned judge against Step 14's fresh snapshot:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles judge --snapshot <step-14-measure-output>
```

- **Exit 0** — the plan file names the judge worker. Proceed to Step 16.
- **Non-zero naming the judge's headroom** — the window cannot cover a
  ruling. Report it and finish here. There is no substitute judge, no
  fallback to another model, and no degraded ruling.
- **Any other non-zero** — report the diagnostic and finish here. Never
  hand-write an assignment to bypass the refusal.

## Step 16 — Start the Judge Worker on Its Pinned Tier

Run:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/start-judge-worker.sh" \
  <step-15-plan-file> <pane> [claude|codex|grok]
```

Starts the plan's pinned judge and verifies launch argv. Pane text never
proves the tier; the script header owns the detailed contract.

- **Exit 0** — the launch argv proved the tier. Its JSON names the agent, model
  and effort. Proceed immediately to Step 17.
- **Any non-zero** — report the diagnostic and finish here without briefing
  the worker or overriding its tier.

## Step 17 — Dispatch the Judge

Use Step 15's plan under Step 10's dispatch contract:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply \
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
  lead never edits the branch itself. At an exhausted allowance, record the
  checkpoint and concrete correction proposal through the owner commands in
  `skills/herdr-teamlead/references/dispatch-recovery.md`. Report implementation
  as `waiting_for_operator` while the bounded decision is pending; finish here
  until it arrives. Record an explicit approval once and continue within it.
  Otherwise
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
the brief is fresh, and wait on the report in Step 11. A source-changing
release finding returns to Step 12 for the next counted developer assignment.
The worker merges after all gates pass. Proceed immediately to Step 21 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 21 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`. Proceed immediately to Step 22.

## Step 22 — Log the Round

Finalize the task ledger with the round outcome and remaining obligations.
Mark the task completed only after its acceptance criteria and required
release and cleanup obligations are verified. Preserve the ledger for resume
and standup. Report its absolute path and the outcome. Finish here.

For the daily standup, use
`Skill(skill: "herdr-standup")`.
