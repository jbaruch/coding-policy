---
name: eval-authoring
description: >
  Generate, review, and curate eval scenarios for Tessl skills. Handles scenario
  generation, bleeding/leaking detection, criteria quality checks, coverage gap
  analysis, and score-driven iteration.
  Use when creating test cases or test scenarios for a skill, evaluating or
  assessing skill quality, running evals or evaluations, reviewing existing
  evals, expanding eval coverage, or skill testing.
---

# Eval Authoring Skill

Generate, review, and iterate on eval scenarios for a Tessl skill. The 10-step workflow: generate (1) and download (2–3) scenarios, audit each (4) for bleeding/leaking, fix (5) or delete (6) unsalvageable ones, fill coverage gaps (7), run evals (8), interpret results via lift analysis (9), iterate until stable (10). Steps are sequential — complete each before moving to the next.

## Step 1 — Generate Scenarios

```bash
tessl scenario generate .
```

## Step 2 — Wait for Generation

```bash
tessl scenario view <id>
```

Poll until completed. If it fails, report the error and finish here. When status is completed, proceed immediately to Step 3.

## Step 3 — Download Scenarios

```bash
tessl scenario download --output evals <id>
```

## Step 4 — Review Each Scenario

For every scenario in `evals/`, read `task.md` and `criteria.json`. Check against `skills/eval-authoring/REVIEW_CHECKLIST.md`: does the task describe a situation without prescribing the technique? Do the criteria grade the specific manner the tile prescribes (good) rather than restating literals from the task (bleeding)? Any tile-internal leaks in the criteria? Are criteria values public surfaces, tile-prescribed conventions (allowed — they measure tile value), or tile internals (leaking)? Any quality or consistency issues?

If no issues found in a scenario, proceed silently to the next one. Proceed immediately to Step 5.

## Step 5 — Fix Issues

Edit `criteria.json` and `task.md` to remove bleeding, remove leaking, improve failure messages, and align criteria with task. See `skills/eval-authoring/REVIEW_CHECKLIST.md` for definitions.

When a criterion is misaligned, leaking, or unsalvageable, remove it and reweight the remaining criteria so the checklist sums to 100 — do not keep a bad criterion to preserve existing weights. If removing the bad criterion leaves no tile-specific signal, the scenario itself is unsalvageable — delete it per Step 6.

## Step 6 — Delete Unsalvageable Scenarios

Remove scenario directories that can't be fixed: task tests an internal detail, task is too vague, or fixing bleeding would rewrite the entire task.

## Step 7 — Fill Coverage Gaps

Write new scenarios directly rather than re-generating — you have full plugin context, the cloud generator doesn't. Each scenario is a directory in `evals/<name>/` with two files: `task.md` and `criteria.json`.

`criteria.json` MUST use the weighted-checklist wrapper — the scorer rejects bare arrays:

```json
{
  "context": "<one-paragraph rationale: what this scenario tests and why it measures tile value (not baseline reasoning)>",
  "type": "weighted_checklist",
  "checklist": [
    { "name": "<short-criterion-name>", "max_score": <int>, "description": "<what passes and what failure looks like>" }
  ]
}
```

Weights in `checklist[].max_score` MUST sum to exactly 100. Do not distribute evenly — weight the criteria that most specifically grade tile-prescribed behaviour. Look at a sibling `evals/*/criteria.json` in this tile to anchor on the exact shape; ignore any pre-existing plain-array files in the test repo under evaluation — those are seed data, not the format to emit.

After writing each new scenario, run the Step 4 review against it and apply Step 5 fixes before moving on — new scenarios need the same no-bleeding / no-leaking audit as generated ones, and the failure mode on this step is to skip review on content you authored yourself.

## Step 8 — Run Evals

```bash
tessl eval run .
```

If any scenario fails to run, diagnose and fix before proceeding.

## Step 9 — Analyze Results (Lift, Not Attainment)

For each scenario, compute `lift = with_context_score - baseline_score`. Lift is the number that matters — aggregate attainment alone is a vanity metric.

Classify each scenario by its lift band, run the three-cause diagnosis on weak / no-lift positive cases, decide whether negative-case lift is healthy or null, and triage failing criteria into a skill / task / criteria fix per the reference at:

```text
skills/eval-authoring/LIFT_ANALYSIS.md
```

Bring the results back here, then proceed immediately to Step 10.

## Step 10 — Iterate

Fix the identified issues — including retiring null-test scenarios — then re-run from Step 8. Repeat until every positive-case scenario shows meaningful lift and every criterion grades behaviour the tile actually contributes. Finish here when the lift distribution is stable and no null tests remain.
