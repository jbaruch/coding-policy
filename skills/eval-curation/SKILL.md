---
name: eval-curation
description: >
  Prune, trim, and shape an existing Tessl eval suite. Run the suite, compute
  per-scenario lift, apply the three-cause diagnosis to near-zero-lift
  scenarios, decide keep / fix / retire, and verify the curated suite still
  pulls weight.
  Use when an eval suite has grown bloated, scenarios are producing near-zero
  lift, reviewing an existing suite for trim opportunities, optimizing a suite
  for cost / signal ratio, many scenarios feel redundant or low-value, or the
  user says trim / prune / shape / curate / optimize the evals.
---

# Eval Curation Skill

Prune an existing eval suite down to the scenarios that actually pull weight. Process steps in order. Do not skip ahead.

The diagnostic vocabulary (lift bands, three-cause diagnosis) lives in:

```text
skills/eval-authoring/LIFT_ANALYSIS.md
```

Read it before Step 3. The obligation to prune is set by `rules/plugin-evals.md` "Lift, Not Attainment".

## Step 1 — Run the Suite

```bash
tessl eval run .
```

Wait until the run completes. Record the run ID — Step 2 needs it.

Proceed immediately to Step 2.

## Step 2 — Pull Per-Scenario Lift

```bash
tessl eval view --json <run-id> | python3 .tessl/plugins/jbaruch/coding-policy/skills/eval-curation/compute-lift.py
```

The script reads a `tessl eval view --json` payload from stdin (or a path argument), pairs each scenario's `with-context`/`usage-spec` variant against its `baseline`/`without-context` variant, sums the `assessmentResults` scores per side, and emits the per-scenario lift trio as JSON:

```json
{
  "lifts": [{ "scenario_id": "<uuid>", "lift": <float>, "with_context_total": <float>, "baseline_total": <float> }],
  "skipped": [{ "scenario_id": "<uuid>", "reason": "<diagnostic>" }]
}
```

Scenarios missing a paired variant land in `skipped` with a diagnostic; the script does not silently drop them. The deterministic JSON walk + arithmetic live in the script per `rules/script-delegation.md` so this step is reproducible and testable independently of any agent.

Proceed immediately to Step 3.

## Step 3 — Classify by Lift Band

Bucket each scenario into the lift bands defined in:

```text
skills/eval-authoring/LIFT_ANALYSIS.md
```

Healthy positive-case bands and negative cases whose near-zero lift is acceptable per that file's Negative Cases section stay as-is.

If no scenarios sit in the actionable bands (no weak / no-lift positive cases, no plugin-specific negative case that fails the lift expectation), the suite is clean. Produce a one-line `curation-summary.md` stating "no curation needed" and finish here — do not fabricate diagnoses for scenarios that don't need them.

Otherwise proceed immediately to Step 4 with two inputs to the three-cause diagnosis: the weak / no-lift positive cases, AND any plugin-specific negative case whose lift fell below the acceptable band.

## Step 4 — Diagnose Every Actionable Scenario

Apply the three-cause diagnosis from `rules/plugin-evals.md` "Lift, Not Attainment" to each scenario routed in from Step 3:

1. **Coincidence with universal competence** — plugin's prescribed manner matches what baseline agents produce by default (positive case), or for a plugin-specific negative case: baseline refuses for plugin-independent reasons so the plugin's refusal adds no signal. Decision: retire
2. **Task leaked the technique** — fix the task per `skills/eval-authoring/REVIEW_CHECKLIST.md`'s No Bleeding rules; keep the criterion. Do NOT drop the criterion.
3. **Criteria grade universal competence** — the criteria test things baseline always does (basic git safety, obvious engineering judgement), not plugin-specific choices. Decision: rewrite the criteria to test the specific manner the plugin prescribes, or retire the scenario.

Record the decision per scenario: `retire`, `fix-task`, or `rewrite-criteria`. Proceed immediately to Step 5.

## Step 5 — Apply Decisions

For each `retire`: `git rm -r evals/<scenario-dir>` and note the removal at the top of the plugin's `CHANGELOG.md`, following the project's CHANGELOG Hygiene convention (`rules/context-artifacts.md`).

For each `fix-task`: edit `task.md` per the No Bleeding rules — strip the technique / format / literal that leaked; keep the situation the user actually needs done.

For each `rewrite-criteria`: edit `criteria.json` so the checklist grades the specific manner the plugin prescribes (flag choices, format literals, sequences, conventions), not universal competence. Re-weight so `max_score` values still sum to exactly 100; if removing a criterion leaves nothing plugin-specific, retire the scenario instead.

Proceed immediately to Step 6.

## Step 6 — Verify the Curated Suite

Re-run the suite (`tessl eval run .`) and re-fetch per-scenario lift via Step 2's mechanism. Verify three things: retired scenarios are gone from the run, fixed scenarios now show meaningful lift, the lift distribution is denser than before the curation pass.

If any fixed scenario still produces near-zero lift, return to Step 4 with that scenario alone — the diagnosis was wrong or the fix didn't take. Otherwise finish here when the distribution is stable and every kept scenario contributes signal.
