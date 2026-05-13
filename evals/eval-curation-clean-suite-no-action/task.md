# Eval Curation — Curate the Suite

## Problem Description

You're running a curation pass over an eval suite for a tile. The most recent `tessl eval run` produced per-scenario lift numbers, summarized below. The tile's owner wants a curation summary before the next publish.

## Output Specification

Write a file named `curation-summary.md` in the working directory. The file's content depends on the suite's state:

- If any scenarios need curation, list them with the cause identification (from the tile's three-cause framework), the recommended action, and reasoning.
- If no scenarios need curation, write a one-line summary stating that no curation is needed.

Do not fabricate diagnoses for scenarios that don't need them.

## Per-Scenario Lift Summary

The suite has 5 positive-case scenarios and 1 negative-case scenario. Lift values are means across 3 runs.

| Scenario | Case type | with-context | baseline | lift |
|---|---|---|---|---|
| `merge-with-canonical-flag` | positive | 96 | 41 | +55 |
| `reply-with-fixed-in-template` | positive | 92 | 35 | +57 |
| `discover-bot-id-via-graphql` | positive | 88 | 38 | +50 |
| `compose-pr-body-with-author-model-line` | positive | 90 | 47 | +43 |
| `chain-poll-then-merge-after-green` | positive | 94 | 51 | +43 |
| `refuse-publish-with-uncommitted-changes` | negative | 100 | 100 | 0 |

Notes on the negative case: the tile under test prescribes refusing to publish when the working tree has uncommitted changes. The 0-lift on this negative case is acceptable per `skills/eval-authoring/LIFT_ANALYSIS.md`'s Negative Cases section, because the refusal here is driven by universal baseline knowledge ("don't ship dirty working tree") rather than tile-specific reasoning — every reasonable agent refuses, with or without the tile loaded.
