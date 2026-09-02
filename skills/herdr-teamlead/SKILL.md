---
name: herdr-teamlead
description: >
  Run one task round as the team lead of three coding agents inside Herdr
  (https://herdr.dev): measure each worker's subscription headroom, assign the
  developer / tester / reviewer roles to the workers that can afford them,
  clear each worker's context and send it a fresh role brief, wait for the
  report files, gate the round, and hand the merge to the `release` skill.
  Use when the user wants to run a team round, dispatch a task to the agent
  team, assign roles to the Herdr agents, load-balance the agents by usage or
  headroom, brief the developer / reviewer / tester, or collect the workers'
  reports. Requires HERDR_ENV=1.
---

# Herdr Team Lead Skill

Process steps in order. Do not skip ahead.

You are the lead of three coding agents running in Herdr panes: a developer, a
reviewer/architect, and a tester. You assign the roles, write the briefs, read
the reports, and gate the round. You never edit the shared checkout, and you
never write code for a worker.

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

## Step 1 — Verify Herdr and the Roster

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/roster.sh
```

Takes no arguments. Emits `{"caller":{"pane_id":...},"agents":[{"name","kind","pane_id","state"}]}`,
listing every named live agent other than your own pane.

- **Exit 0 with a populated `agents`** — proceed to Step 2.
- **Exit 0 with `agents: []`** — nobody is named. Report the unnamed panes from
  `herdr agent list` and the `herdr agent rename <pane-id> <name>` command that
  fixes it, then finish here.
- **Exit 1** — the precondition failed (outside Herdr, or `herdr` absent).
  Report the message verbatim and finish here.
- **Exit 2** — herdr failed. Report the message verbatim and finish here.

Fewer named workers than roles is a decision, not a detail: either name another
agent or fold two roles onto one worker in that worker's brief, and say which
you did.

## Step 2 — Verify Authority for the Repo

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/verify-authority.sh <owner/repo>
```

Emits `{"repo","viewer_login","owner_login","owner_type","viewer_permission","namespace_owner","authorized"}`.
`authorized` reflects namespace ownership alone; write permission never sets it
(`rules/external-repo-contributions.md` Default Deny).

- **`authorized: true`** — record the authority line for the briefs:
  `owner of <owner/repo>`. Set `EXTERNAL_PERMISSION` to `none`. Proceed to
  Step 3.
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

Proceed immediately to Step 3.

## Step 3 — Measure Headroom

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh measure
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
before relying on its role. Proceed immediately to Step 4.

## Step 4 — Plan the Roles

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh plan --roles developer,tester,reviewer
```

Pure computation over the newest snapshot plus the assignment ledger. Contacts
no agent, writes nothing. Emits `{"assignments":{"<role>":"<agent>"},"rationale":[...],"snapshot_ref":{...}}`.
Exit 1 names the reason it could not plan. The `--roles` order is load-bearing;
what it means and how ties break are in `skills/herdr-teamlead/teamlead/planner.py`
(the `plan` docstring).

Save the output to a file for Step 5. Relay the `rationale` lines to the user
as the round's role announcement. Proceed immediately to Step 5.

## Step 5 — Compose the Briefs

Write a values file for the round, then compose:

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/compose-briefs.sh \
  .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/templates \
  <values.json> <round-reports-dir>
```

The values file is `{"shared": {...}, "roles": {"<role>": {...}}}`; a role's
own value beats the shared one. Emits
`{"common":"<path>","briefs":{"<role>":"<path>"}}`. Exit 2 means validation
failed and nothing was written — an unfilled placeholder, a supplied key no
template uses, or a value that is not text. Exit 3 means the placeholder scan
itself failed, so whether the briefs are clean is unknown: re-run, never
dispatch on it. The placeholder set and both validation directions are the
script's contract; see the header of
`skills/herdr-teamlead/compose-briefs.sh`.

What you decide, and it is the whole of your job here:

- `SHARED_CHECKOUT` — the checkout the workers read.
- `AUTHORITY_STATEMENT` and `EXTERNAL_PERMISSION` — verbatim from Step 2. Never
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

A worker starts each round with a cleared context. A brief that says "see the
reviewer's earlier comment" reaches an agent that cannot see it. Name the
issue, the file, the finding, and the path in full. Proceed immediately to
Step 6.

## Step 6 — Provision the Worktrees

One call per worker that writes anything:

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/provision-worktree.sh \
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
dispatch a brief whose worktree does not exist. Proceed immediately to Step 7.

## Step 7 — Label the Layout (optional, once per team)

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/label-workspaces.sh \
  <lead-label> [<agent>=<workspace-id>]...
```

Names the lead's workspace, each worker's workspace after its agent, and each
worker's pane after its kind. With no pairs, the workspaces come from the
roster. Emits `{"lead":{...},"agents":[...]}` with a per-target
`renamed|unchanged|failed`. Exit 3 means at least one rename failed and the
JSON says which — labels are cosmetic, so that never stops a round.

Run this once per team, not once per round: a name already in place is
reported `unchanged` and nothing is sent. Skip it on a team whose sidebar is
already named. Proceed immediately to Step 8.

## Step 8 — Dispatch the Briefs

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --common <path-to-COMMON.md> [--task <label>]
```

Clears each worker's context and hands it its brief. Emits one JSON object:
per role a record carrying `cleared`, `landed`, `started`, and `status`.

Each outcome names where the round goes next. Only a dispatched worker can
produce a report, so Step 9 waits on exactly the roles that landed here.

- **Exit 0** — every role was dispatched. Proceed to Step 9.
- **Non-zero with a busy target** — the whole round was refused before any
  keystroke went out. Nothing was dispatched, so there is nothing to wait for:
  wait for that worker to reach idle and re-run this step, or re-run it with a
  plan that omits the busy worker. Do not go to Step 9.
- **Non-zero with `"status": "sent_but_not_started"`** — the message was sent
  and no turn began for that role. Read that worker's pane; do not re-dispatch
  on top of it. Go to Step 9 for the roles whose records say `started`, and
  treat this role as producing no report this round.
- **A worker failed on its clear command** — its assignment was never sent, and
  the round is short that role. Go to Step 9 for the rest; re-dispatch this one
  by re-running this step for that role alone once its pane is clear.
- **A refusal saying the screen did not change** — the clear command was
  consumed and the pane did not redraw, so the context was not cleared and
  `apply` sent that worker nothing: a brief goes out only behind a confirmed
  clear. Read the pane and clear it by hand. Once the pane shows a fresh
  session, re-run this step for that role with `--no-clear`; that is the one
  path on which a record carries `cleared: false`, and it means the lead did
  the clearing. Never brief a worker on top of the previous task's context.
- **A refusal naming an unaccounted composer** — the worker's input line holds
  text the lead did not send. Nothing was dispatched for that role. Read the
  pane and clear it by hand, or re-run this step with `--allow-recovery` once
  you know whose text it is. Codex sends no recovery key at all: its clear key
  exits an idle Codex.
- **A refusal the message does not cover** — report it verbatim and finish
  here. A dispatch nobody understands is not a round to wait on.
- `--dry-run` prints every command it would run and makes no herdr calls. It
  dispatches nothing, so finish here after reading it.
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

Proceed to Step 9 with the roles that were dispatched.

## Step 9 — Wait for the Reports

One call per dispatched worker, in the order the round needs them:

```bash
.tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/wait-report.sh <agent-name> <report-path>
```

Emits `{"agent","state","report_path","found","elapsed_seconds"}` on every
outcome. Completion requires both the report file on disk and the `REPORT: `
marker in the worker's pane. A single `idle` or `done` observation is not
completion. Poll interval and give-up budget are the script's own constants.

- **Exit 0** — the report is there. Continue to the next worker, then Step 10.
- **Exit 1** — the budget ran out. Read the pane with
  `herdr agent read <name> --source visible`. If the worker is still working,
  re-run this step for it: the script's own budget applies again, never a
  number chosen here. Otherwise go to Step 10 recording that it produced no
  report.
- **Exit 2** — a tool failure. Report the message verbatim and finish here; the
  round has no reliable view of any worker.
- **Exit 4** — the report file exists and the worker reads idle on consecutive
  polls, but the pane never showed the marker in a shape the script
  recognizes. Read the pane with `herdr agent read <name> --source visible`.
  A `REPORT: ` line naming this report, in any wrapping, confirms it: treat the
  report as delivered and continue to the next worker. Anything else is a
  worker that wrote the file and did not finish: keep waiting by re-running
  this step, or record it as producing no report.
- **Exit 3** — the worker is blocked at an approval or question dialog,
  confirmed across two reads and the pane. Read the dialog with
  `herdr pane read <pane-id> --source visible`, relay its text to the operator
  verbatim, and stop the round for that worker. You never answer it: the
  operator does. Resume only once `herdr agent get <name>` reports a state
  other than `blocked`, then re-run this step for that worker.

Proceed to Step 10 once every dispatched worker has been waited on, or once you
have recorded which of them produced no report.

## Step 10 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.

- **Any blocking finding** — run another round: return to Step 3 with fresh
  briefs naming the findings in full. Say what changed from the prior round.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

The release hand-off has one condition, and all four parts are required:

1. The developer's report names the branch and the commit SHA it pushed.
2. A reviewer **Mode B** report reviews that same SHA and carries no blocking
   finding.
3. A tester **Mode C** report verifies that same SHA, with the repo's gates run
   and every acceptance criterion met.
4. Nothing has been pushed to the branch after those two reports.

A Phase 1 design note or test plan does NOT satisfy 2 or 3
(`rules/agent-team-operation.md` Review Before PR). A report against an older
SHA does not either — re-run Phase 2 against the current tip.

With all four met, the release is one more assignment, never a prompt into
the developer's existing context: return to Step 5 with the role `release` for
the developer's agent (template `templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 6 (it reports
`already-provisioned`), dispatch through Step 8 so the context is cleared and
the brief is fresh, and wait on the report in Step 9. The worker merges; you
do not. After its report, fast-forward the shared checkout, remove the
worktree, and delete the branch per `rules/agent-worktree-isolation.md`.

Log the round: the assignments, the report paths, the findings, and the
outcome. If the round produced no findings at all, log it and say so in one
line rather than reproducing the reports. Finish here.

For the daily standup, which is a different round shape entirely, use
`Skill(skill: "herdr-standup")`.
