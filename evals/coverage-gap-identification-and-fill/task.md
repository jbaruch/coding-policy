# Eval Coverage Gap Analysis

## Problem/Feature Description

A team has built a Tessl skill called `deploy` that automates a deployment workflow. The skill picks different paths based on the target environment, runs post-deploy verification before declaring success, and emits notifications about the outcome.

Their existing eval suite has two scenarios, both happy paths: one deploys to staging and reports success, the other deploys to production with an approval flag set and also reports success. The team suspects coverage is thin but isn't sure exactly what's missing.

They ask you to audit the eval coverage, identify what the existing two scenarios don't exercise, and write new scenarios that fill those gaps.

## Output Specification

- Produce `coverage-analysis.md` describing the gaps you identified and why each one matters.
- For each gap, create a new scenario directory under `evals/`. The file layout and criteria format should follow this tile's conventions for eval scenarios — consult the tile's own rules and existing scenarios rather than guessing.
