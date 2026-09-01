---
name: teamlead
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

# Team Lead Skill

Process steps in order. Do not skip ahead.

You are the lead of three coding agents running in Herdr panes: a developer, a
reviewer/architect, and a tester. You assign the roles, write the briefs, read
the reports, and gate the round. You never edit the shared checkout, and you
never write code for a worker.

Herdr command surface, worker quirks, and the usage-parsing details:

```text
skills/teamlead/references/herdr.md
```

Role weights, report reading, and the pre-PR review sequence:

```text
skills/teamlead/references/round-flow.md
```

## Step 1 — Verify Herdr and the Roster

```bash
.tessl/plugins/jbaruch/coding-policy/skills/teamlead/roster.sh
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

## Step 2 — Measure Headroom

```bash
.tessl/plugins/jbaruch/coding-policy/skills/teamlead/teamlead.sh measure
```

Sends each configured worker its own usage command and parses the reply. Emits
one snapshot document — per agent: `kind`, `state`, `herdr_state`,
`state_source`, `windows`, `credits`, `plan`, `headroom_pct`, `skipped` — and
appends it to the state file (`skills/teamlead/state-schema.md`). A worker that
is `working` or `blocked` is reported as skipped with null windows, never
interrupted. A `working` verdict is confirmed against the pane before it counts:
`state_source` names which signal decided, `herdr` or `probe`, and `herdr_state`
carries what herdr claimed. Exit 1 means at least one agent could not be
measured; the snapshot still prints and names it in `failed_agents`.

The usage marker is confirmed in the text that gets parsed, never in a wait
alone. `--marker-poll-attempts` and `--marker-poll-interval` bound the
confirming poll; `--marker-timeout` and `--lines` size the pane read. Attempt
counts and intervals default to the script's own constants; see
`skills/teamlead/teamlead/measure.py`.

Add `--trace` (or `TEAMLEAD_TRACE=1`) when a live run does something the JSON
does not explain: every herdr invocation, its exit status, and its raw output
go to stderr, and stdout stays the machine-readable document.

Report a `failed_agents` entry to the user and measure that worker by hand
before relying on its role. Proceed immediately to Step 3.

## Step 3 — Plan the Roles

```bash
.tessl/plugins/jbaruch/coding-policy/skills/teamlead/teamlead.sh plan --roles developer,tester,reviewer
```

Pure computation over the newest snapshot plus the assignment ledger. Contacts
no agent, writes nothing. Emits `{"assignments":{"<role>":"<agent>"},"rationale":[...],"snapshot_ref":{...}}`.
Roles are passed heaviest-first; the ordering rules live in
`skills/teamlead/teamlead/planner.py`.

Save the output to a file for Step 5. Relay the `rationale` lines to the user
as the round's role announcement. Proceed immediately to Step 4.

## Step 4 — Write the Round's Briefs

Fill one brief per assigned role from the templates:

```text
skills/teamlead/templates/brief-developer.md
skills/teamlead/templates/brief-reviewer.md
skills/teamlead/templates/brief-tester.md
```

Substitute every placeholder: `{{ISSUE}}`, `{{BRANCH}}`, `{{WORKTREE}}`,
`{{REPORT}}`, `{{REPORTS_DIR}}`. Give each worker its own worktree path under
`~/.worktrees/` and its own report path under the round's reports directory.

Copy `skills/teamlead/templates/COMMON.md` into the round's reports directory
once per repo, substituting `{{SHARED_CHECKOUT}}`. Every worker reads the same
copy.

A worker starts each round with a cleared context. A brief that says "see the
reviewer's earlier comment" reaches an agent that cannot see it. Name the
issue, the file, the finding, and the path in full. Proceed immediately to
Step 5.

## Step 5 — Dispatch the Briefs

```bash
.tessl/plugins/jbaruch/coding-policy/skills/teamlead/teamlead.sh apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --common <path-to-COMMON.md>
```

Per role: re-reads the worker's live state, sends its native context-clear
command, waits for it to settle, then sends the assignment prompt and records
the hand-off in the ledger. Emits one JSON object describing what was sent.

- A `working` or `blocked` worker makes the command refuse the whole round
  before a single keystroke goes out, with a JSON error on stderr and a
  non-zero exit. Wait for that worker, or dispatch a round without it.
- A `working` verdict is confirmed against the pane first, so a stale window
  title does not refuse a worker sitting at an empty prompt.
- `--dry-run` prints every command it would run and makes no herdr calls. The
  pane confirmation appears separately under `conditional_commands`.
- The prompt text and the `--force` override are the utility's own contract;
  see `skills/teamlead/teamlead/assign.py`.

Proceed immediately to Step 6.

## Step 6 — Wait for the Reports

One call per dispatched worker, in the order the round needs them:

```bash
.tessl/plugins/jbaruch/coding-policy/skills/teamlead/wait-report.sh <agent-name> <report-path>
```

Emits `{"agent","state","report_path","found","elapsed_seconds"}` on every
outcome. Completion requires both the report file on disk and the `REPORT: `
marker in the worker's pane. A single `idle` or `done` observation is not
completion. Poll interval and give-up budget are the script's own constants.

- **Exit 0** — the report is there. Continue to the next worker.
- **Exit 1** — the budget ran out. Read the pane with
  `herdr agent read <name> --source visible` before re-dispatching.
- **Exit 2** — a tool failure. Report the message verbatim and stop the round.
- **Exit 3** — the worker is blocked at an approval or question dialog. Read
  the dialog, surface it to the user, and answer it only with the user's
  consent when it authorizes anything outside the brief.

Proceed immediately to Step 7 once every dispatched worker has been waited on.

## Step 7 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.

- **Any blocking finding** — run another round: return to Step 2 with fresh
  briefs naming the findings in full. Say what changed from the prior round.
- **Branch green, tester and reviewer clear** — prompt the developer to run
  `Skill(skill: "release")` Steps 1–4, which opens the PR and requests the
  bots. After the bots post, prompt it to run Steps 5–7 for the merge and
  cleanup. The developer merges; you do not.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

Log the round: the assignments, the report paths, the findings, and the
outcome. If the round produced no findings at all, log it and say so in one
line rather than reproducing the reports. Finish here.
