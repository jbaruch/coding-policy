# Eval Curation — Curate the Suite

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, summarized below. The tile's owner wants a curation summary before the next publish.

## Output Specification

Write a file named `curation-summary.md` in the working directory. The file's content depends on the suite's state:

- If any scenarios need curation, list them with the cause identification (from the tile's three-cause framework), the recommended action, and reasoning.
- If no scenarios need curation, write a one-line summary stating that no curation is needed.

Do not fabricate diagnoses for scenarios that don't need them.

## Per-Scenario Lift Summary

Lift values are means across 3 runs.

| Scenario | with-context | baseline | lift |
|---|---|---|---|
| `merge-with-canonical-flag` | 96 | 41 | +55 |
| `reply-with-fixed-in-template` | 92 | 35 | +57 |
| `discover-bot-id-via-graphql` | 88 | 38 | +50 |
| `compose-pr-body-with-author-model-line` | 90 | 47 | +43 |
| `chain-poll-then-merge-after-green` | 94 | 51 | +43 |
| `refuse-publish-with-uncommitted-changes` | 100 | 100 | 0 |
