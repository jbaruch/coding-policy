---
alwaysApply: true
description: Running a multi-agent team — headroom-driven role rotation, one writer per worktree, report files as the only channel, dispatch safety, internal review before the PR
---

# Agent Team Operation

## Roles and Rotation

- A team round runs three roles: developer, reviewer/architect, tester
- Roles rotate between tasks
- Rotation follows measured subscription headroom through the team skill's script, never an impression of who looks fresh
- Headroom is the minimum remaining window per worker, never the average
- Each assignment clears the worker's context, then sends a fresh role brief
- Fewer live workers than roles is a decision to record, never a silently dropped role

## Writers and Checkouts

- One writer per worktree
- The shared checkout stays on the default branch
- The lead reads the shared checkout and never edits it
- The lead provisions every worker's worktree before dispatch
- A worker never creates, moves, or removes a worktree
- A worker runs no git command against the shared checkout, mutating or otherwise
- A worker writes only in the worktree its brief names, under `~/.worktrees/`
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

## Policy Parity

- Every worker runs the same plugin from the shared checkout
- A runtime that does not auto-load the rules reads them from its brief
- The brief names the rule index and the release-skill path in full
