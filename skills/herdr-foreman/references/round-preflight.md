# Round Preflight

Read this contract when the execution plan reaches one of its steps. Execute
only the current step and the continuation it names. Step numbers refer to
`skills/herdr-foreman/SKILL.md`.

## Step 2 — Run the Round Preflight

One call answers every deterministic check a round start owes: Herdr and the
roster, authority for the repo, measured headroom, the capability table's
cadence, and worktree hygiene.

First run the capability owner's local schema upgrade. It preserves evidence
and creates no missing table; no consultation or operator approval is needed.
Exit 0 emits the schema-2 capability-table JSON documented in
`skills/herdr-foreman/references/successor-placement.md`, including `entries`
and `successors`. Non-zero emits an actionable refusal on stderr; report it
and stop this round start.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" capability-migrate
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/round-preflight.sh" \
  --repo <owner/repo> --checkout <shared-checkout>
```

Emits one JSON object: `ready`, the `blocking` reasons, the cadences that are
`due`, and each check's own payload under `checks`. Exit 1 is a verdict, not a
failure — something blocks the round. Exit 2 means the preflight could not
answer.

Before following any route below, report the worktree sweep to the operator:

- When `checks.worktrees.detail` is present, relay its `report` verbatim
- The sweep builds that report (`skills/herdr-foreman/sweep-worktrees.sh`,
  `report_text`); never reshape or summarize it
- When `checks.worktrees` has no `detail`, report its `reason` verbatim; a
  status `ok` with no `detail` means the worktree root does not exist, and
  there is nothing to report for it
- On exit 2 there is no JSON; report the stderr diagnostic instead
- Raise each `dirty` or `unpushed` item the report lists per
  `rules/hook-action-reporting.md` Act on What It Names; the operator carries
  out the resolution chosen, and the foreman runs none of it
- An item outside the selected checkout remains visible in attention but never
  blocks this task
- `ready` carries any hygiene failure that affects the selected checkout

- **Exit 0** — record `due` as maintenance and proceed to Step 5 without waiting
  on it.
  A checkpointed or resumed foreman proceeds to the stow's continuation step
  instead (Step 17 Continue Route or Resume Route).
  Refresh a due capability table at the next maintenance checkpoint under
  `skills/herdr-foreman/references/model-tiers.md`. A selection-time fact about
  a seat this task needs may block that seat; cadence alone never does. The
  maintenance consultation revisits `successors_due` under
  `skills/herdr-foreman/references/successor-placement.md`.
  - Record keep, revise or withdraw outcomes through `capability-record`.
  - Preserve unknown outcomes as unknown.
  - Create no automatic recalibration job.

  The maintenance
  commands record the report and show the result:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" capability-record --record <report.json>
```

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" capability-show
```

For an authorized verified successor, read
this reference before editing config:

```text
skills/herdr-foreman/references/successor-placement.md
```

Record its existing spot through the owner:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" capability-successor --record <successor-report.json>
```

Exit 0 emits the saved provisional-placement JSON described by the referenced
schema, including its origin, provenance and empty history. Non-zero refuses the cited field
or evidence; repair it without relabeling qualification or changing pins.

- **Exit 1** — report the `blocking` reasons verbatim. Each names the command
  that produced it; re-run that one, not the preflight. A `foreman_tier` block
  beside a failed `headroom` check waits on that measurement; fix it first.
  A named `foreman relaunch-worker <name>` recovery follows
  `skills/herdr-foreman/references/model-tiers.md` Maintenance Relaunch.
  After that recovery, re-run the preflight.
  Otherwise it means this pane does not run the foreman's selected tier:
  record a user-attention blocker naming `start-foreman`
  (`skills/herdr-foreman/references/model-tiers.md` Foreman Seat) and finish here.
- **Exit 2** — report the diagnostic and finish here.

On exit 0 or 1, a `checks.foreman_tier` status `unconfigured` blocks nothing:
relay its `detail.warning` verbatim before routing.

The blocker quotes the restart the operator runs from another shell, naming an
empty Herdr shell pane; never run it from the foreman's own pane:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" start-foreman --pane <pane-id>
```

Which checks run, and which exit codes they fold into `blocking`, are the
script's decision contract — see `skills/herdr-foreman/round-preflight.sh`, not
restated here (`rules/script-as-black-box.md`).

Steps 3 and 4 remain the individual commands, for a caller that needs one on its
own. A round start runs this instead of all of them. The roster has no step of
its own; inspect it directly when only the live workers are wanted:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/roster.sh"
```

Emits the caller and live workers with kind, pane, and state. Under schema 7 an
empty worker roster is the expected idle state. Apply creates workers only for
planned assignments. If `herdr agent list` shows other unnamed panes, report
them separately; never turn them into a standing roster.

Record staffing gaps under `skills/herdr-foreman/references/round-setup.md`. Leave unused specialist
profiles unlaunched. Never duplicate targets or fold verification onto a
contributor. Apply starts schema-7 workers in YOLO mode under
`skills/herdr-foreman/references/model-tiers.md`;
preserve it on relaunch. Verify live permission flags before dispatch, including
existing workers. Record task authorization and permitted actions under the
round-setup reference. Create or resume the stable ledger under
`skills/herdr-foreman/references/task-ledger.md`; record its absolute path before dispatch. Apply the
round-setup accepted-behavior, resume and supervision binding requirements.

Check `skills/herdr-foreman/references/retrospectives.md` on resume, before
planning, or for an explicit retrospective request. Daily cadence is visible
maintenance and never blocks the selected task. Missing coverage still blocks
the exact worker transition it protects. For an explicit request, complete a
new retrospective and finish here.

Proceed immediately to Step 5, or after a checkpoint or resume to the stow's continuation step.

## Step 3 — Verify Authority for the Repo

Step 2 runs this. Use it alone when only the authority answer is wanted.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/verify-authority.sh" <owner/repo>
```

Record the emitted namespace ownership evidence using Step 3 of
`skills/herdr-foreman/references/round-setup.md`. For a non-owned repo, reuse explicit per-action
operator permission; absent permission, remain read-only or finish here.
On non-zero, report the diagnostic and finish here.

Proceed immediately to Step 4.

## Step 4 — Measure Headroom

Step 2 runs this. Use it alone to re-measure, which the judge round does.

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" measure
```

Emits and saves headroom, windows, state, `tier_billing`, and `failed_agents`.
With schema 7 it sends no usage keystrokes to assignment panes. Probe and
window-group mechanics live in
`skills/herdr-foreman/foreman/lifecycle.py` (`measure_worker_kinds`).
Legacy busy standing workers are skipped.
Unmeasured billing stays `unknown`. Report failed
measurements and follow Step 4's Probe and startup recovery route in
`skills/herdr-foreman/references/round-setup.md` before relying on those seats. A pending
CLI update follows `skills/herdr-foreman/references/model-tiers.md`
Maintenance Relaunch.

Usage and `--trace` contracts:

```text
skills/herdr-foreman/references/round-setup.md
```

Proceed immediately to Step 5 once the required readings are available.
