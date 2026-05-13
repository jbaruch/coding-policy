# Eval Curation — Diagnose a Near-Zero-Lift Scenario

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, and one positive-case scenario is sitting at near-zero lift after multiple runs. The tile's owner needs a diagnosis report and a recommended action before the next publish.

The scenario under review is below. The tile this scenario belongs to is a commit-convention tile; the criterion expects the agent to produce a commit message in imperative mood. The lift signal is **+1.2 points** (with-context 92, baseline 90.8) across three runs — i.e., baseline agents already produce imperative-mood commits at essentially the same rate as agents with the tile loaded.

## Output Specification

Write a file named `diagnosis.md` in the working directory containing:

1. **Cause identification** — name the cause of the near-zero lift (use the canonical name from the tile's eval-curation guidance).
2. **Recommended action** — one of `retire`, `fix-task`, `rewrite-criteria`.
3. **Reasoning** — one or two sentences citing why the cause is what you identified, and why the chosen action is the one prescribed for that cause.

Do not edit the scenario itself in this task — your output is the diagnosis report only.

## Input Files

=============== FILE: evals/scenario-imperative-mood/task.md ===============
# Write a Git Commit Message

You've just added a function that validates email addresses against RFC 5322. Author a single git commit that captures this change. Produce the commit message body only (no `git commit` invocation).

=============== FILE: evals/scenario-imperative-mood/criteria.json ===============
{
  "context": "Tests whether the agent produces a commit message in imperative mood, per the commit-conventions tile.",
  "type": "weighted_checklist",
  "checklist": [
    {
      "name": "imperative mood",
      "max_score": 60,
      "description": "The first line of the commit message uses imperative mood (e.g., 'Add', 'Validate', 'Implement') rather than past tense ('Added', 'Validated') or present participle ('Adding', 'Validating')"
    },
    {
      "name": "subject line under 72 chars",
      "max_score": 40,
      "description": "The subject line is 72 characters or fewer"
    }
  ]
}
