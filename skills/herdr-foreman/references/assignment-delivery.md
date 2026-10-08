# Assignment Delivery

Read this contract when the execution plan reaches one of its steps. Execute
only the current step and the continuation it names. Step numbers refer to
`skills/herdr-foreman/SKILL.md`.

## Step 7 — Provision the Worktrees

Step 2's preflight swept every repository with a worktree directory under
the root, every round, and Step 2 reported its outcomes. Route on its
`checks.worktrees.status`; the classification is
`skills/herdr-foreman/round-preflight.sh`'s (the `checks.worktrees` comment
at the top of the file):

- `ok` or `degraded` — provision
- any other status — do not provision; report its `reason` and detail,
  repair what it names, then run Step 2's preflight again with
  `--no-measure` and route on the new status

Never remove a worktree by hand; Step 15's removal of the merged task's own
worktree is the one exception. To re-sweep without provisioning (Step 15),
run the sweep alone and relay its `report` verbatim, as Step 2 does:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/sweep-worktrees.sh" "$HOME/.worktrees"
```

Input is the worktree root. It removes the spent worktrees and local branches
of every repository with a worktree under that root. Stdout is one JSON
object whose `report` is the operator-facing summary; the full shape is the
script's top-of-file contract.

- **Exit 0** — every repository decided cleanly. Relay `report`, raise each
  `dirty` or `unpushed` item it lists, and continue.
- **Exit 2** — JSON is present; at least one repository's prune failed or a
  path could not be read. Every other repository still ran, unless the
  root changed mid-sweep: then the prune in flight stopped its removals,
  and the error and failure lines name the repositories and steps left
  undone.
  - Relay `report`; its failure and error lines name each one.
  - Repair what they name.
  - Run the sweep again.
- **Exit 1** — no JSON; a precondition is unmet. Report the stderr
  diagnostic, repair what it names, then run the sweep again.

The removal predicates live in `skills/herdr-foreman/prune-worktrees.sh`
(top-of-file docstring).

Then run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, exact base and fetched-default commits, and
`created|attached|already-provisioned`; it persists their Git-owned provenance.
Fetch failure stops before worktree creation. Pass an existing task's original
authorized base as `base-ref`; never refresh that task base for a correction. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only consultations need none. Clean up after merge per
`rules/agent-worktree-isolation.md`. Proceed immediately to Step 8.

## Step 8 — Compose the Briefs

Resolve policy paths through the Step 8 reference first. Write its outputs in
`shared` within `{"task":"<existing task id>", "state":"<owner state path when non-default>", "shared": {...}, "roles": {"<role>": {...}}}` and run:

`GATES` is shared: Step 2's `checks.gates.detail.brief`, verbatim. On a
non-empty `checks.gates.detail.missing`, name those paths in the round's report.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/compose-briefs.sh" \
  "$CP/skills/herdr-foreman/templates" \
  <values.json> <round-reports-dir>
```

Emits common and role-brief paths. On non-zero, fix the diagnostic before
dispatch. Validates composition inputs and review evidence before writing.
Use a fresh absolute report path per role and attempt.

Follow `skills/herdr-foreman/references/round-setup.md` Step 8 for shared and role-specific values,
authority, review evidence and brief completeness.

- Follow `skills/herdr-foreman/references/team-operation.md` Judgment Routes
- When accepted behavior, reproducible failure and a bounded correction are
  already recorded, compose the development brief directly from that evidence;
  no additional advisor report is required
- Unsettled behavior, evidence, scope or correction choice requires the named
  consultation
- Substantive foreman judgment remains prohibited
- When consultation is required, return to Step 5 until its framing is accepted,
  then set `SPECIALIST_CONTEXT` to its assessed report's absolute path
- Otherwise leave `SPECIALIST_CONTEXT` empty; do not invent a consultation report
- Never copy, excerpt or paraphrase a consultation report into a value
- Triggered specialty work, causal uncertainty, changed scope and exhaustion keep
  their required consultation routes

Proceed immediately to Step 9.

## Step 9 — Label the Layout

Optional, once per team; skip an already named sidebar.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/label-workspaces.sh" \
  <lead-label> [<agent>=<workspace-id>]...
```

Emits per-target `renamed|unchanged|failed`; exit 3 names partial failures.
Report label failures and continue. Proceed immediately to Step 10.

## Step 10 — Dispatch the Briefs

Complete the retrospective reference's cadence and transition checks before live
dispatch. Apply rechecks coverage before worker input. Dry runs prove no coverage.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" apply \
  --assignments <plan-file> \
  --brief <role>=<path> [--brief <role>=<path>]... \
  --report <role>=<report> [--report <role>=<report>]... \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --retain-specialist | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

Emits dispatch JSON under `state-schema.md`. Supply each role's fresh absolute
report path from its brief. Apply enrolls before input; unknown sends remain
observation obligations. Apply refuses while an open decision or blocker on the
task is unanswered; see the attention reference's Dispatch gate.
Classify every brief against Step 3's authorization before sending it.
Apply refuses a consultation brief without a contiguous `CRITERION 1..N` block
under `## Acceptance Criteria`. That block is the `N` its report answers.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the recovery reference's Dispatch context requirements before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone. Warm consultations use the recovery reference's
`--retain-specialist` path. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

Follow the Dispatch Results contract in `skills/herdr-foreman/references/round-flow.md` for busy,
uncertain, failed, and dry-run outcomes. Preserve all already enrolled work.

Dispatch references:

```text
skills/herdr-foreman/references/dispatch-recovery.md
skills/herdr-foreman/references/model-tiers.md
```

Proceed to Step 11.
