# Eval Curation — Diagnose a Near-Zero-Lift Scenario

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, and one positive-case scenario is sitting at near-zero lift after multiple runs. The tile's owner needs a diagnosis report and a recommended action before the next publish.

The scenario under review is below. The tile this scenario belongs to is a merge-workflow tile; the criterion expects the agent to use a specific merge flag the tile prescribes. The lift signal is **+2.4 points** (with-context 94, baseline 91.6) across three runs — agents without the tile loaded perform almost as well as agents with the tile loaded.

## Output Specification

Write a file named `diagnosis.md` in the working directory containing:

1. **Cause identification** — name the cause of the near-zero lift (use the canonical name from the tile's eval-curation guidance).
2. **Recommended action** — one of `retire`, `fix-task`, `rewrite-criteria`. If you choose `fix-task`, include the rewritten task body inline.
3. **Reasoning** — one or two sentences citing why the cause is what you identified, why the chosen action is the one prescribed for that cause, and (specifically for this cause) whether the criterion itself should be kept or dropped.

Do not edit the scenario files directly; produce the diagnosis report (with the proposed task rewrite inline if applicable).

## Input Files

=============== FILE: evals/scenario-merge-flag/task.md ===============
# Merge a Pull Request

You've been asked to merge PR #42 into `main`. The team's convention is to use `--ff-only` for clean merges so the branch history stays linear. Produce the exact `gh pr merge` invocation you would run.

=============== FILE: evals/scenario-merge-flag/criteria.json ===============
{
  "context": "Tests whether the agent uses the team's prescribed merge flag, per the merge-workflow tile.",
  "type": "weighted_checklist",
  "checklist": [
    {
      "name": "uses ff-only",
      "max_score": 70,
      "description": "The `gh pr merge` invocation includes `--ff-only` (the team's prescribed merge mode)"
    },
    {
      "name": "references PR 42",
      "max_score": 30,
      "description": "The invocation targets PR 42 (`gh pr merge 42 --ff-only` or equivalent)"
    }
  ]
}
