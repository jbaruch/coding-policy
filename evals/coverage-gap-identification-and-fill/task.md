# Eval Coverage Gap Analysis

## Problem/Feature Description

A team has built a Tessl skill called `deploy` that automates a deployment workflow. The skill has three decision points:

1. **Environment gate**: the skill checks whether the target is `staging` or `production`. Staging deploys proceed automatically; production deploys require a manual approval flag.
2. **Health check**: after deployment, the skill polls a health endpoint. If the service is healthy, it reports success. If unhealthy, it triggers a rollback.
3. **Notification**: the skill sends a Slack message summarizing the outcome.

The team generated eval scenarios and currently has two:

- **Scenario A** ("happy-path staging deploy"): task asks to deploy to staging, criteria check that it proceeds without approval, runs health check, reports success
- **Scenario B** ("production deploy with approval"): task asks to deploy to production with the approval flag set, criteria check that it proceeds, runs health check, reports success

The team asks you to review the eval coverage, identify what's missing, and write new scenario directories to fill the gaps. Each new scenario needs a `task.md` and `criteria.json` following the standard weighted checklist format.

## Output Specification

1. Produce a file named `coverage-analysis.md` listing the gaps you identified and why each matters
2. For each gap, create a new scenario directory (e.g., `evals/scenario-c/`, `evals/scenario-d/`) containing `task.md` and `criteria.json`

Focus on decision branches that aren't covered by the existing scenarios. Do not recreate scenarios A or B.
