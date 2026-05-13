# Eval Curation — Diagnose a Near-Zero-Lift Scenario

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, and one positive-case scenario is sitting at near-zero lift after multiple runs. The tile's owner needs a diagnosis report and a recommended action before the next publish.

The scenario under review is below. The tile this scenario belongs to is a deployment-orchestration tile that prescribes a specific staging sequence (canary 10% → bake 15min → full rollout) and a specific rollback trigger (error-rate delta > 0.5% in any 5-min window). The lift signal is **+0.8 points** (with-context 88, baseline 87.2) across three runs.

## Output Specification

Write a file named `diagnosis.md` in the working directory containing:

1. **Cause identification** — name the cause of the near-zero lift (use the canonical name from the tile's eval-curation guidance).
2. **Recommended action** — one of `retire`, `fix-task`, `rewrite-criteria`. If you choose `rewrite-criteria`, include the proposed replacement criteria inline (weights summing to 100); if you choose `retire`, explain why nothing tile-specific can be salvaged.
3. **Reasoning** — one or two sentences citing why the cause is what you identified, and why the chosen action is the one prescribed for that cause.

Do not edit the scenario files directly; produce the diagnosis report.

## Input Files

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
