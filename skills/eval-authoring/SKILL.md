---
name: eval-authoring
description: >
  Generate, review, and curate eval scenarios for Tessl skills. Handles scenario
  generation, bleeding/leaking detection, criteria quality checks, coverage gap
  analysis, and score-driven iteration.
  Use when creating test cases for a skill, evaluating skill quality, reviewing
  existing evals, or expanding eval coverage.
---

# Eval Authoring Skill

Generate, review, and iterate on eval scenarios. Steps are sequential — complete each before moving to the next.

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

When a criterion is misaligned, leaking, or otherwise unsalvageable, **remove it** and reweight the remaining criteria so the checklist still sums to 100 — do NOT keep a bad criterion just to preserve the existing weights. Bumping a misaligned criterion's `max_score` to keep the math tidy makes the scenario worse, not better. If a fix removes the only criterion that gave the scenario tile-specific signal, the scenario itself is unsalvageable — delete it per Step 6.

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

For each scenario, compute `lift = with_context_score - baseline_score`. Lift is the number that matters — aggregate attainment on its own is a vanity metric (a tile scoring 99% with-context and 73% baseline is contributing 26 points of real value, not 99).

- **Lift < 10 on a positive case** → diagnose one of three causes. (a) Coincidence with universal competence: the tile prescribes what baseline already does; the rule is documentation, not lift-producing — accept or retire. (b) Task leaked the technique: baseline pattern-matched — fix the task (strip the leaked literal), keep the criterion. (c) Criteria grade engineering-101 rather than the specific tile-prescribed manner — rewrite the criteria to check the tile's specific prescription, not to test "reasoning" that baseline already does
- **Lift 10–30 on a positive case** → weak. Audit for the three causes above
- **Lift ≥ 40 on a positive case** → healthy signal. The tile is doing real work — usually these scenarios check specific tile-prescribed choices (particular bot-ID discovery, particular reply template, particular CLI sequence). Keep them; do NOT soften them toward "testing reasoning"
- **Negative cases**: near-zero lift is acceptable only when the baseline refusal is driven by universal knowledge (obvious error cases). Tile-specific refusal reasoning must still show lift

For each scenario with non-zero lift but with-context below 100%, identify the failing criteria and decide: is the problem in the **skill** (unclear instruction), the **task** (doesn't ask for what criteria test), or the **criteria** (tests the wrong thing)?

When the analysis is complete, proceed immediately to Step 10.

## Step 10 — Iterate

Fix the identified issues — including retiring null-test scenarios — then re-run from Step 8. Repeat until every positive-case scenario shows meaningful lift and every criterion grades behaviour the tile actually contributes. Finish here when the lift distribution is stable and no null tests remain.
