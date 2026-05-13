# Eval Curation — Diagnose a Near-Zero-Lift Scenario

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, and one positive-case scenario is sitting at near-zero lift after multiple runs. The tile's owner needs a diagnosis report and a recommended action before the next publish.

The scenario under review is below, along with an excerpt of the tile's rules (you'll need both to do the curation). The lift signal across three runs: **with-context 88, baseline 87.2 (lift +0.8)**.

## Output Specification

Write a file named `diagnosis.md` in the working directory containing:

1. **Cause identification** — name the cause of the near-zero lift (use the canonical name from the tile's eval-curation guidance).
2. **Recommended action** — one of `retire`, `fix-task`, `rewrite-criteria`. Note that the cause-#3 rule prescribes `retire` only when nothing tile-specific can be salvaged from the scenario; check whether the task above supplies replacements before choosing the fallback. If you choose `rewrite-criteria`, include the proposed replacement criteria inline (weights summing to 100).
3. **Reasoning** — one or two sentences citing why the cause is what you identified, and why the chosen action is the one prescribed for that cause.

Do not edit the scenario files directly; produce the diagnosis report.

## Input Files

=============== FILE: rules/deployment-orchestration.md ===============
# Deployment Orchestration

## Staging Sequence

All production deployments follow a fixed sequence:

1. Roll out to canary (10% of traffic).
2. Bake for 15 minutes — no rollout advancement during this window.
3. Promote to full traffic after the bake completes cleanly.

## Rollback Trigger

Auto-rollback fires when the error-rate delta versus the prior version exceeds 0.5% in any 5-minute window. The rollback reverts traffic to the prior version atomically; the deployment plan must name the prior version as the rollback target.

=============== FILE: evals/scenario-deployment/task.md ===============
# Roll Out a New Service Version

A new version of the `orders-api` service is ready to ship. The previous version is currently serving 100% of production traffic. Plan a deployment that gets the new version live without breaking the production SLO. Produce a file named `deploy-plan.md` describing the steps you would take.

=============== FILE: evals/scenario-deployment/criteria.json ===============
{
  "context": "Tests whether the agent produces a sound deployment plan for the orders-api version rollout, per the deployment-orchestration tile.",
  "type": "weighted_checklist",
  "checklist": [
    {
      "name": "mentions deployment",
      "max_score": 40,
      "description": "The deploy-plan.md mentions deploying the new version of the service"
    },
    {
      "name": "considers rollback",
      "max_score": 35,
      "description": "The deploy-plan.md mentions rollback as a concept (any form — having a rollback plan, being able to revert, etc.)"
    },
    {
      "name": "addresses production",
      "max_score": 25,
      "description": "The deploy-plan.md acknowledges the production environment in some form"
    }
  ]
}
