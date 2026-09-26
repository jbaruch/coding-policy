# Judge Round Reference

The seven steps of one judge round, run in order from `skills/herdr-foreman/SKILL.md`
Step 13. Modes, triggers and both report contracts are in
`skills/herdr-foreman/references/round-flow.md` "The Judge".

Each command resolves `CP` to the local or home plugin, the same way SKILL.md does.
Repeat its resolver in every call.

## 1 — Compose the Judge Brief

Optional. Modes, triggers and both report contracts are in
`skills/herdr-foreman/references/round-flow.md` "The Judge" (a bot
disagreement inside SKILL.md Step 14 returns here first). No trigger — proceed to
SKILL.md Step 14.

For a dispute, compose from `templates/brief-judge.md` through SKILL.md Step 7: the
dispute, both positions with report paths, the governing rule, the tree. Fill
`POSITION_A_EVIDENCE` and `POSITION_B_EVIDENCE` with the citations each report
makes (file and line, or command output, at a revision), copied, never supplied by the
foreman. A position that cites nothing is not ready for the judge: dispatch an
investigator under `skills/herdr-foreman/references/specialists.md` to
establish the disputed facts with citations first, fill that position's
evidence value with `supplied by the investigator report`, and fill
`INVESTIGATION_REPORT` with that report. Fill
`INVESTIGATION_REPORT` with the investigator's report after an `insufficient`
ruling as well, otherwise "none".

For an exhausted allowance, compose from `templates/brief-judge-diagnosis.md`
through SKILL.md Step 7 under the role key `judge-diagnosis`, which writes
`brief-judge-diagnosis.md`: the assessed investigator report, the task, rounds
spent, remaining blocking work, the per-round history, the tree, and any prior
remedy with what it changed.
The pinned seat is still `judge`, so plan and dispatch that role and pass this
file as its brief: `--brief judge=<outdir>/brief-judge-diagnosis.md`.

Skip SKILL.md Step 8 for the read-only judge. Proceed immediately to step 2.

## 2 — Re-measure the Shared Window

Re-run SKILL.md Step 4's `measure` under its outcome contract. Resolve any unreadable
judge window before planning. Pass the fresh snapshot to step 3; never reuse
the earlier reading as affordability proof. Proceed immediately to step 3.

## 3 — Plan the Judge Seat

Plan the pinned judge against step 2's fresh snapshot:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-foreman/foreman.sh" plan \
  --roles judge --judge-mode <adjudication|diagnosis> \
  --snapshot <step-2-measure-output> --task <task-id>
```

`adjudication` for a dispute, `diagnosis` for an exhausted allowance — the same
choice step 1 made when it composed the brief. Use the recorded mode in steps
4 and 5. An undeclared mode is refused.

Exit 0 names the judge worker; proceed immediately to step 4. On non-zero,
report the diagnostic and finish here. Never substitute a judge, lower its tier,
or hand-write an assignment to bypass the refusal.

## 4 — Start the Judge Worker on Its Pinned Tier

For an existing judge worker, proceed to step 5 with a clearing dispatch.
For an empty shell pane, complete retrospective checks for the start and run:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-foreman/start-judge-worker.sh" \
  <step-3-plan-file> <pane> [claude|codex|grok] --task <task-id> [--state <state-file>]
```

Starts the pinned judge and verifies launch argv, on the mode step 3 recorded
in the plan. The header owns the contract.

- **Exit 0** — proceed immediately to step 5 with `--no-clear`.
- **Any non-zero** — report the diagnostic and finish here without briefing
  the worker or overriding its tier.

## 5 — Dispatch the Judge

Use step 3's plan under SKILL.md Step 10's dispatch contract:

```bash
CP=.tessl/plugins/jbaruch/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-foreman/foreman.sh" apply \
  --assignments <plan-file> \
  --brief judge=<round>-judge.md --report judge=<absolute-report-path> \
  --common <path-to-COMMON.md> --judge-mode <adjudication|diagnosis> \
  --task <task-id> [--no-clear]
```

Pass the same `--judge-mode` step 3 planned. SKILL.md Step 10's outcomes govern. Use
`--no-clear` only for the worker just started in
step 4; an existing judge receives the default cleared relaunch with retrospective
coverage. Apply verifies the live tier before input. Proceed immediately to step 6.

## 6 — Wait for the Ruling

Run SKILL.md Step 11's fleet observation loop, including the judge named by step 3.
Proceed immediately to step 7 once its report lands; keep other enrollments
under observation.

## 7 — Act on the Ruling or Remedy

Apply the Ruling Outcomes contract in `skills/herdr-foreman/references/round-flow.md`. Investigation
rulings return to SKILL.md Step 12's knowledge gate. Implementation rulings route
unchanged-branch rulings to verified release or renewed verification,
branch-changing rulings to the counted correction path, an `insufficient`
ruling to an investigator and then back to step 1, and a blocked ruling to its
saved operator question. Only the operator overrides a completed ruling; `insufficient` binds nothing and creates no checkpoint.
A judge round is a round: log it in SKILL.md Step 16 and reset in Step 17,
recording the step that outcome names as the stow's continuation step. The
next context takes SKILL.md Step 17's Resume Route.
