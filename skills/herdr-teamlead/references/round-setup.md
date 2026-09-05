# Round Setup Reference

Detailed setup contracts and examples for Steps 2–9 of `skills/herdr-teamlead/SKILL.md`.
The skill retains the execution order and continuation gates.

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
  [--exclude <role>=<agent>[,<agent>...]]... \
  [--round <role>=<round-type>] [--round-context <evidence.json>] [--fix-round <N>]
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

Tiered configs select each candidate from its per-agent `tiers` table. A
round choice never overrides a model. Supply the fix number when planning
fixes; pass that same number at dispatch. Keep the configured operator launch
options across worker restarts. The config, round-input, billing-evidence,
and qualification contracts are in:

```text
skills/herdr-teamlead/references/model-tiers.md
```

Save the output to a file for dispatch. Relay the `rationale` lines to the user
as the round's role announcement: they name the weight behind each seat, the
exclusions applied, and any worker whose headroom reading is stale. Proceed
immediately to Step 6.

Default planning excludes unqualified tiers. Use `plan --preview-tiers` only to inspect an uncommissioned table; live dispatch still requires qualification.

## Step 6 — Build the Review Package

For reviewer or tester briefs, run from a checkout containing the recorded
commits:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/review-package.sh \
  <recorded-base-sha> <pushed-head-sha> <round-reports-dir>/review-<base7>..<head7>.diff
```

Record the pre-round tip before the first development dispatch. Preserve that
SHA as the task's base through every fix. A full review uses that base and the
current pushed tip; a scoped re-check uses the preceding reviewed tip as its
base. Never infer the base from `HEAD~1`. Set `REVIEW_BASE` and `REVIEW_HEAD`
to those full SHAs. Rebuild for each changed range, including the final full
review after scoped fixes.

Before development, use the recorded base for both endpoints. That empty-range
package is planning input, never evidence of a verified implementation.
Developer-only, release, and judge briefs need no package; proceed to Step 7.

Success prints only the absolute artifact path, not JSON. The file contains
the resolved range, commit list, stat, and patch. Set `TEAMLEAD_REPORTS_DIR`
to use the default range-specific filename instead of passing OUTFILE.
Exit 2 names invalid input; exit 1 names a tool or output failure. On either,
fix the named cause and repeat; never compose verification briefs without a
completed package. Existing different content is preserved.

Proceed immediately to Step 7 with the printed path as `REVIEW_PACKAGE`.

## Step 7 — Compose the Briefs

Write a values file for the round, then compose:

```bash
bash .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/compose-briefs.sh \
  .tessl/plugins/jbaruch/coding-policy/skills/herdr-teamlead/templates \
  <values.json> <round-reports-dir>
```

The values file is `{"shared": {...}, "roles": {"<role>": {...}}}`; a role's
own value beats the shared one. Emits
`{"common":"<path>","briefs":{"<role>":"<path>"}}`. Exit 2 means validation
failed and nothing was written — an absent or unreadable review package,
an unfilled placeholder, a supplied key no template uses, a value that is not
text, or a `REPORT` longer than the
script's limit (the worker's `REPORT: <path>` line must fit one pane row for
Step 11 to confirm it; use a short reports directory). Exit 3 means the placeholder scan
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
- For reviewer and tester: `REVIEW_PACKAGE`, `REVIEW_BASE`, and `REVIEW_HEAD`
  from Step 6. Missing, empty, or non-file package paths refuse composition
  before any brief is written.

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
release-gating verification is `full`. Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

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
dispatch a brief whose worktree does not exist. Proceed immediately to Step 9.

## Step 9 — Label the Layout (optional, once per team)

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
already named. Proceed immediately to Step 10.
