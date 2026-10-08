# Round Completion

Read this contract when the execution plan reaches one of its steps. Execute
only the current step and the continuation it names. Step numbers refer to
`skills/herdr-foreman/SKILL.md`.

## Step 13 — Run the Judge Round

Optional. No trigger — proceed to Step 14. A bot disagreement inside Step 14
returns here first. A weighing nomination from Step 12 is an adjudication
trigger. The judge's report is the ruling once `foreman verify-ruling`
binds it in the judge round's last step; until then no `defer` or `decline`
applies.

Run the round's seven steps in order — compose the brief, re-measure the shared
window, plan the pinned seat, let apply spawn its fresh worker on the pinned tier, dispatch,
wait, act on the ruling:

```text
skills/herdr-foreman/references/judge-round.md
```

The judge is read-only, so Step 7 is skipped for it. Never substitute a judge,
lower its tier, or hand-write an assignment to bypass a refusal. Its last step
names where to continue.

A judge round is a round: once its ruling is recorded, run Step 16 and Step
17 before continuing, whatever the ruling (`insufficient` and `blocked`
included). Record the step the ruling named as the stow's continuation step.
The reset foreman takes Step 17's Resume Route.

## Step 14 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 7 (it reports `already-provisioned`), then
Step 8 with the role `release` for the developer's agent (template
`skills/herdr-foreman/templates/brief-release.md`, the same `WORKTREE` and `BRANCH`, a fresh
`REPORT`), dispatch through Step 10 so the context is cleared and
the brief is fresh, and wait on the report in Step 11. `apply` refuses the
release dispatch, dry run included, while the task carries an open verdict
gate: return to Step 12 and clear it first. A source-changing
release finding returns to Step 12 for the next counted developer assignment.
A blocking policy review returns there too, where its nominations go to a
weighing. Fill `WEIGHING_RULING` with the judge's report and the follow-up
issue once a weighing covers the findings, otherwise "none".
The worker merges after all gates pass. Proceed immediately to Step 15 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 15 — Clean Up the Worktree

Fast-forward the shared checkout, remove the merged task's own worktree with
`git worktree remove`, and delete the branch, in the post-merge order of
`rules/agent-worktree-isolation.md` Cleanup. This is the one removal the
foreman makes itself (`skills/herdr-foreman/references/team-operation.md` Writers and Checkouts,
the merged-task exception). Then run Step 7's sweep again for the round's other
worktrees. Proceed immediately to Step 16.

## Step 16 — Log the Round

Finalize the task ledger with the round outcome and remaining obligations.
Mark the task completed only after its acceptance criteria and required
release and cleanup obligations are verified. When the task merged or was
abandoned, close it. The record is
`{"task", "outcome": "merged" | "abandoned", "evidence"}`:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" close-task --record <close.json>
```

- **Exit 0** — stdout is the recorded `task_closed` event JSON. Repeating the
  same closure prints the existing event. Proceed.
- **Non-zero** — stderr names the refused field or the conflicting earlier
  closure. Correct the record and re-run; do not finish Step 16 with a merged
  or abandoned task unclosed.

A task still in progress (a consultation or a fix round) is not closed; it
continues to Step 17 open.

Preserve the ledger for resume and standup. Preserve retrospective notes and link them from the ledger. Save
current progress through the attention owner and curate the round's lessons.
Report outstanding attention first, followed by the outcome and saved paths.
Proceed immediately to Step 17.

## Step 17 — Reset the Foreman Context

Stow the handoff under the working-memory reference, with a structured gap
for anything the stow could not capture. The stow's `unresolved_work` names
the continuation step, the step the round's outcome routes to:

- A gate decision, judge ruling or remedy — the step it named
- A release-ready pull request — Step 14
- A merged or abandoned task awaiting closure — Step 16
- Only `foreman-queue` seats remaining — Step 5

Handle every pending supervision event, and save `supervision-hold` kind
`handoff` covering each active enrollment. Use the stow id as the hold id.
This prepares reset preflight; it does not permit Stop on its own. Then
schedule the reset:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" foreman-reset --stow <stow-id> \
  [--state <state-file>] [--config <config-file>] [--herdr-bin <path>]
```

Before running the reset, read:

```text
skills/herdr-foreman/references/working-memory.md — Reset Outcome Routing
```

Follow its exit and asynchronous-failure routes. A
failed reset never permits Stop with active work.

**Resume Route** — the next context follows one route:

1. The resume prompt's reads: the stow and its required files, supervision,
   `foreman-queue`
2. Step 1, then Step 2
3. The stow's continuation step, in place of Step 5

`foreman-queue` lists seats only. Gating, release and closure return through
the continuation step, never through the queue.

Finish here.

For the daily standup, use
`Skill(skill: "herdr-standup")`.
