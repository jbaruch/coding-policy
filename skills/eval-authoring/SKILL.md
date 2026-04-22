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

For every scenario in `evals/`, read `task.md` and `criteria.json`. Check against `skills/eval-authoring/REVIEW_CHECKLIST.md` for bleeding, leaking, quality, and consistency issues.

If no issues found in a scenario, proceed silently to the next one.

## Step 5 — Fix Issues

Edit `criteria.json` and `task.md` to remove bleeding, remove leaking, improve failure messages, and align criteria with task. See `skills/eval-authoring/REVIEW_CHECKLIST.md` for definitions.

## Step 6 — Delete Unsalvageable Scenarios

Remove scenario directories that can't be fixed: task tests an internal detail, task is too vague, or fixing bleeding would rewrite the entire task.

## Step 7 — Fill Coverage Gaps

Write new scenarios directly rather than re-generating — you have full plugin context, the cloud generator doesn't. Each scenario is a directory in `evals/` with `task.md` and `criteria.json` (weighted checklist with `name`, `description`, `max_score` per criterion).

Repeat Steps 4–6 for new scenarios.

## Step 8 — Run Evals

```bash
tessl eval run .
```

If any scenario fails to run, diagnose and fix before proceeding.

## Step 9 — Analyze Results

For each with-context score below 100%, identify the failing criteria and decide: is the problem in the **skill** (unclear instruction), the **task** (doesn't ask for what criteria test), or the **criteria** (tests the wrong thing)?

Baseline and with-context both high (90%+) on positive cases means the eval tests general knowledge, not skill value — acceptable for negative cases only.

## Step 10 — Iterate

Fix the identified issues, then re-run from Step 8. Repeat until with-context scores reflect the skill's guidance. Finish here when scores are stable.
