# Round Gate

Read this contract when the execution plan reaches one of its steps. Execute
only the current step and the continuation it names. Step numbers refer to
`skills/herdr-foreman/SKILL.md`.

## Step 12 — Gate the Round

Classify every delivered report in one call, and save its stdout:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/classify/classify-reports.sh" <report>... > <labels.json>
```

On exit 2 (usage error), fix the arguments stderr names and rerun before using
`<labels.json>`. A report in the output's `unannotated` list gets no gate and is
gated exactly as it would have been.

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
A report's recorded `VERDICT:` and `ACCEPTANCE` lines decide it. Never accept,
reject or rate a finding on your own reading.
Then record the gates the labels earn, before gating any report:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" report-gate-record --labels <labels.json>
```

- Exit 0 prints one JSON object: `recorded`, `replayed` and `no_gate` lists
- Exit 1: nothing is recorded; resolve the cause stderr names before gating any report
- `close-member` and `record-report` refuse while a gate forbids the decision
- `assess-specialist` and `record-report` record a verdict gate for every
  `VERDICT: blocking` report
- A verdict gate never refuses acceptance
- `apply` refuses a fresh `release` dispatch while the task carries an open
  verdict gate
- A `block` gate clears only through `report-gate-clear`
- A `reread` gate clears only through `report-gate-reread`
- A label never approves, accepts or skips a check

Output fields, gate levels and the evidence each resolution cites are the
owners' contract:

```text
skills/herdr-foreman/references/report-classifier.md
```

Gate the reports together in one turn, not one turn per report.
Before accepting a mechanical round, compare its whole result against the
oracle its plan declared. `<result-file>` is the pushed diff for a `patch`
oracle and the produced output otherwise:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" verify-oracle \
  --plan <plan-file> --role <role> --result <result-file> --task <task>
```

Exit 0 is a match. Exit 1 with `"match": false` is a blocking finding on the
round; exit 1 with no verdict is a usage error to resolve before gating,
including a plan whose oracle differs from the one the round's dispatch bound.
Before accepting a partitioned responsibility's pass, confirm its plan, as
dispatched, still covers exactly the task's diff at the tip under review:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" verify-partition \
  --plan <plan.json> --repo <repo-path> --head <tip-under-review> --task <task>
```

- Exit 0 confirms it
- Exit 1 names what failed: another repo or base, a stale head, an edited
  boundary, a seat never dispatched or dispatched with another boundary, or
  paths the slices leave unowned, no longer cover, or own twice
- Exit 1 also names a seat whose latest dispatch is not this plan's applied
  send to its assigned worker, or a plan made without `--task`
- Exit 1 is a blocking finding on the round, gated below like any other
- Run Step 16, then Step 17, with Step 4 as the stow's continuation step
- The reset foreman resumes at Step 4 and re-validates the partition at the
  tip in Step 5, replanning from that result
- The new plan then takes Step 8 composition, Step 10 dispatch, Step 11
  observation, and this step's gate

Only now, with every report's gates recorded, record each assignment's outcome
in the task ledger from its recorded contract lines, citing the report:

- `accepted` for a reviewer or tester needs a recorded valid `VERDICT:` at the
  current report bytes, whatever its value; for a consultation it needs every
  `ACCEPTANCE` line `met`. The verdict gates the round below, never acceptance
- A contract gap, or an `unmet` criterion, is `needs_work`
- A developer's work rests on the reviewer's and tester's verdicts

Record the task's gate decision separately; a worker finishing its brief never
completes the whole task. Once the ledger records an assignment's assessed
outcome, close its enrollment in one call:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"; [ -d "$CP" ] || case "$(git config --get remote.origin.url)" in git@github.com:jbaruch/coding-policy|git@github.com:jbaruch/coding-policy.git|https://github.com/jbaruch/coding-policy|https://github.com/jbaruch/coding-policy.git|ssh://git@github.com/jbaruch/coding-policy|ssh://git@github.com/jbaruch/coding-policy.git) CP=. ;; *) echo "coding-policy plugin not found: run tessl install jbaruch/coding-policy" >&2; exit 1 ;; esac
bash "$CP/skills/herdr-foreman/foreman.sh" close-member --enrollment <enrollment-id> \
  --ledger <absolute-TASK-LEDGER.md>
```

It refuses until the ledger's latest event for that worker and report carries
an assessed decision, and refuses `accepted` without the report's recorded
contract lines, checked before its classifier gates. It verifies a schema-7
assignment pane remains bound to the recorded identity, then closes it with
live absence proved before the enrollment resolves. Schema-7 workers never
retain a pane across assignments; a follow-up round carries prior evidence in
its self-contained brief and receives a fresh identity. A repeated close
accepts an already-absent identity and replays. Resolution stays
separate from assignment acceptance and task completion.
Resume Step 11's fleet watch for any enrollment still observed.
Route correction-scope and bug-evidence assessment to a worker under
`skills/herdr-foreman/references/team-operation.md` Judgment Routes; never
assess them yourself.
Persist user-facing obligations under `skills/herdr-foreman/references/attention.md` before presenting
them; record an actual answer or resolution separately from showing the item.

Every return to Step 4 is a round boundary: run Step 16 to log the round and
Step 17 to reset first. Record the step this gate decision named as the stow's
continuation step. The reset foreman takes Step 17's Resume Route.

After accepting a consultation, return to Step 4 for the next needed
responsibility. For an investigation-only task, use the knowledge gate below.

For an investigation-only task, gate every assigned report on its recorded
`ACCEPTANCE` lines. Resolve a blocking verdict through the same bounded and
judge paths below. Once every criterion is met, present the findings and
preserve open user decisions; proceed to Step 15 if a task worktree needs cleanup, otherwise
Step 16. No implementation or release is inferred from the diagnostic result.

- **Any blocking verdict** — a recorded `VERDICT: blocking` from a reviewer,
  tester, or `security`, `ux-product` or `documentation` consultation is a
  blocking finding for the round. Apply the round-flow reference's Blocking
  Gate contract and `skills/herdr-foreman/references/team-operation.md` Fix Loops. Return to
  Step 4 for an authorized correction or Step 13 for a required judge ruling.
- **A ruled blocking verdict** — a completed adjudication (`uphold` or `amend`)
  decides it. A finding a weighing ruled `defer` or `decline` is settled only
  by the next reviewer or tester report at the tip, marking it DECLINED with
  the ruling and recording `VERDICT: approved`. Never match rulings to
  findings yourself.
- **Clearing a verdict gate by re-check** — once the same responsibility's
  re-check records `VERDICT: approved`, run `report-gate-clear --report
  <blocking report> --evidence <re-check> --reason <what it settled>` for each
  blocking report.
- **Clearing a verdict gate by decision** — an operator's resolved decision
  clears it through `--decision`.
- **No judge clear** — a judge's report is refused as evidence.
- **A contradicting gate** — a classifier `block` gate on a `VERDICT: approved`
  report goes to Step 13 for adjudication.
- **A weighing nomination** — a finding a worker report marks `MARGINAL:`, or
  one `foreman finding-churn` places on lines the previous fix round added,
  goes to Step 13 for a weighing.
- **Nominate, never weigh** — the foreman nominates a finding and never weighs
  it.
- **An exhausted approach allowance** — record the checkpoint through
  `skills/herdr-foreman/references/dispatch-recovery.md`, consult the investigator under
  `skills/herdr-foreman/references/specialists.md` with round context `{"investigator":
  {"diagnosis_input": true}}`, and take its assessed report to Step 13 for the
  diagnosis.
- **No operator wait at exhaustion** — no operator decision is awaited.
- **`VERDICT: approved` with advisory findings** — record them in the round
  log under `rules/review-severity.md` Split Reading From Acting.

Apply the release gate in this reference; obtain broad independent reviewer and
tester passes against the current pushed tip before release:

```text
skills/herdr-foreman/references/round-flow.md
```

With its release criteria met, proceed immediately to Step 13.
