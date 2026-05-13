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

The diagnostic vocabulary (lift bands, three-cause diagnosis) lives in `skills/eval-authoring/LIFT_ANALYSIS.md` — read it before Step 3. The obligation to prune is set by `rules/plugin-evals.md` "Lift, Not Attainment".

This skill is the curation half of the evals workflow. For first-author scenario generation, use `eval-authoring` instead. The two share `LIFT_ANALYSIS.md` and `REVIEW_CHECKLIST.md`.

## Step 1 — Run the Suite

```bash
tessl eval run .
```

Wait until the run completes. Record the run ID — Step 2 needs it.

Proceed immediately to Step 2.

## Step 2 — Pull Per-Scenario Lift

```bash
tessl eval view --json <run-id>
```

For each scenario, compute `lift = with_context_score - baseline_score` from the `solutions` array's `with-context` (or `usage-spec`) and `baseline` variant totals. Record the (scenario, lift) pairs.

If the tile ships a scoring driver (e.g., `scoring/compute-lift.py` in `jbaruch/coding-policy-evals`), use its `--from-tessl-run-id <UUID>` flag to compute the lift trio; otherwise the formula above is sufficient.

Proceed immediately to Step 3.

## Step 3 — Classify by Lift Band

Bucket each scenario into the lift bands defined in `skills/eval-authoring/LIFT_ANALYSIS.md`. Healthy bands stay as-is; weak / no-lift positive cases and ambiguous negative cases need Step 4.

If no scenarios sit in the weak / no-lift bands, the suite is clean. Proceed silently and finish here.

Otherwise proceed immediately to Step 4.

## Step 4 — Diagnose Every Weak / No-Lift Scenario

Apply the three-cause diagnosis from `rules/plugin-evals.md` "Lift, Not Attainment" to each weak scenario:

1. **Coincidence with universal competence** — the tile's prescribed manner matches what baseline agents already produce by default. Decision: retire (or accept as documentation if the criterion has secondary regression-safety value).
2. **Task leaked the technique** — baseline pattern-matched its way to the criterion because the task mentioned it. Decision: fix the task per `skills/eval-authoring/REVIEW_CHECKLIST.md`'s No Bleeding rules; keep the criterion. Do NOT drop the criterion.
3. **Criteria grade universal competence** — the criteria test things baseline always does (basic git safety, obvious engineering judgement), not tile-specific choices. Decision: rewrite the criteria to test the specific manner the tile prescribes, or retire the scenario.

Record the decision per scenario: `retire`, `fix-task`, or `rewrite-criteria`. Proceed immediately to Step 5.

## Step 5 — Apply Decisions

For each `retire`: `rm -rf evals/<scenario-dir>` and note the removal in the tile's `CHANGELOG.md` under Unreleased.

For each `fix-task`: edit `task.md` per the No Bleeding rules — strip the technique / format / literal that leaked; keep the situation the user actually needs done.

For each `rewrite-criteria`: edit `criteria.json` so the checklist grades the specific manner the tile prescribes (flag choices, format literals, sequences, conventions), not universal competence. Re-weight so `max_score` values still sum to exactly 100; if removing a criterion leaves nothing tile-specific, retire the scenario instead.

Proceed immediately to Step 6.

## Step 6 — Re-run and Verify

```bash
tessl eval run .
```

Re-fetch per-scenario lift via Step 2's mechanism. Verify three things: retired scenarios are gone from the run, fixed scenarios now show meaningful lift, the lift distribution is denser than before the curation pass.

If any fixed scenario still produces near-zero lift, return to Step 4 with that scenario alone — the diagnosis was wrong or the fix didn't take. Otherwise finish here when the distribution is stable and every kept scenario contributes signal.
