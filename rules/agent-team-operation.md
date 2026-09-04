---
alwaysApply: true
description: Running a multi-agent team — headroom-driven role rotation, one writer per worktree, report files as the only channel, dispatch safety, internal review before the PR
---

# Agent Team Operation

## Roles and Rotation

- A team round runs three roles: developer, reviewer/architect, tester
- Roles rotate between tasks
- Rotation follows measured subscription headroom through the `herdr-teamlead` skill's script, never an impression of who looks fresh
- Headroom is the minimum remaining window per worker, never the average
- Each assignment clears the worker's context, then sends a fresh role brief
- Fewer live workers than roles is a decision to record, never a silently dropped role

## Judge Seat

- A fifth seat, `judge`, sits outside the three-role rotation on the most capable model available, never assigned developer, reviewer, or tester
- The lead dispatches the judge only for one of four triggers: a contested reviewer or tester verdict, a lead override of a blocking finding, a fix loop that reached its fifth round, or a bot finding the team disagrees with
- The judge is read-only: it never edits a repository file, never runs a mutating git or `gh` command, never posts to GitHub, never dispatches a subagent — its only output is its report file
- The judge reads both positions and the governing rule, verifies the disputed facts against the tree, and returns `RULING: uphold A | uphold B | amend — <line> | blocked — <question>` with numbered reasons, an `ACTION:` naming the minimal step, and an `UNVERIFIED:` line
- The judge's ruling binds the round; only the operator overrides it
- `blocked` is the judge declining to rule; the round stops and the named question goes to the operator
- The judge is declared in `config.json`, measured, and planned like every other seat
- The judge worker and the `claude` worker authenticate as one Claude subscription and draw on one weekly window
- `window_group` names the usage window an agent shares with other agents
- A seat's cost reduces the projected headroom of every worker sharing its `window_group`
- The `judge` block names the seat's agent, and the model and effort its worker is launched with
- The planner seats the judge on the named agent; the model and effort are the worker's launch flags, set when that worker is started
- The planner never ranks the judge seat, and never gives the pinned worker another seat
- No exclusion bars the judge from a dispute involving its own model
- A judge round the pinned worker's window cannot cover halts the round — no substitution, no fallback to another vendor's flagship, no degraded ruling
- The lead runs on the strongest generally-available model at high effort; the most capable model is reserved for the judge

## Writers and Checkouts

- One writer per worktree
- The shared checkout stays on the default branch
- The lead reads the shared checkout and never edits it
- The lead provisions every worktree a brief names, before dispatch
- A read-only role that writes no repository content needs no worktree
- A worker never creates, moves, or removes a worktree
- A worker runs no git command against the shared checkout, mutating or otherwise
- A worker's repository writes happen only in the worktree its brief names, under `~/.worktrees/`
- A worker's report, plan, and patch artifacts go only under the reports directory its brief names
- A worker writes nowhere else
- Every code-touching command carries its own `cd <worktree> &&` prefix
- See `rules/agent-worktree-isolation.md`

## Reports

- A worker's report is a file at the path its brief names
- The final chat message's last line is exactly `REPORT: <path>`
- Substantive output never travels through pane text
- A worker never blocks on a question to the lead
- A worker decides the question itself
- A worker records the decision in its report
- A worker continues after recording it
- A genuine block goes in a `## BLOCKED` section, then the worker stops
- The lead reads every report body in full before gating the round

## Dispatch Safety

- Never send input to a `working` or `blocked` agent
- Never clear a working agent's context
- Wait on the report marker plus the report file, never on a single idle or done observation
- Confirm a `blocked` verdict across two reads and the pane before acting on it
- A blocked worker is surfaced to the operator, never answered on the operator's behalf beyond its brief

## Review Before PR

- A round runs two phases: optional pre-development planning, then mandatory post-push verification
- Pre-development output is a design note or a test plan, never a pass
- The tester and the reviewer pass on the pushed branch before the PR opens
- The gate reads the post-push reports for the current branch tip
- A pre-development report never satisfies the gate
- The developer pushes the branch and stops
- A shared GitHub account posts internal reviews as COMMENT reviews
- The lead enforces the blocking findings a COMMENT review carries
- Severity classification follows `rules/review-severity.md`
- The developer then runs the release skill for the PR, the merge, and the cleanup

## Authority and Policy

- The lead verifies repo authority through a script before composing briefs
- Ownership is namespace ownership; write permission is not ownership
- A brief states the authority as a verified fact, never as a standing claim
- A repo the operator does not own gets explicit per-repo, per-action permission recorded in the brief, or the round stays read-only
- An unanswerable authority check is not permission
- See `rules/external-repo-contributions.md`
- Every worker runs the same plugin from the shared checkout
- A runtime that does not auto-load the rules reads them from its brief
- The brief names the rule index and the release-skill path in full
