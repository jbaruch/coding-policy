# Eval Scenario Review Checklist

## Task and Criteria Shape

Does the task describe a SITUATION or prescribe a TECHNIQUE?

- **Correct**: task describes what the user needs done ("Ship a hotfix", "Wire up a reviewer"). Criteria check whether the output matches the specific manner the tile prescribes — that conformance IS the tile's contribution.
- **Wrong**: task names the technique, format, sequence, or literal the criterion grades ("Ship a hotfix using `--ff-only`"). The agent passes by reading the task, not by applying the tile.

## Bleeding

Two forms:

1. **Task/criterion overlap**: a criterion's expected literal appears verbatim in the task description. For each criterion with a concrete expected value, grep the task text for that literal — a match is bleeding. Fix by stripping the literal from the task and keeping the criterion. Baseline should still be able to attempt the situation (they'll just pick some other manner); if stripping the literal makes the task unsolvable even for a baseline, the scenario is too narrow to evaluate the tile and should be reframed.
2. **Fixture reachable as a skill example**: the scenario's fixture is the same example the skill teaches with. The agent "passes" by recognizing the example, not by applying the lesson. Keep fixtures in a separate namespace from skill examples.

## Leaking

- **Privacy**: use sanitized or synthetic fixtures. Never live user data (real emails, calendar events, production PRs, internal logs). Use stable synthetic IDs and scrubbed examples — live-data fixtures drift silently and risk accidental exposure.
- **Tile internals (leaking)**: criteria must not reference internal skill action names, `.tessl/tiles/...` paths, or tile-only identifiers that mean nothing outside the tile.
- **Public surfaces (allowed)**: `gh pr create`, REST endpoints, conventional-commits format, semver — these exist independent of the tile.
- **Tile-prescribed conventions (allowed)**: specific reply templates (`Fixed in <sha>`), chosen flags (`--ff-only`), invented format literals, specific sequences. A competent engineer without the tile would not produce these specific choices — checking for them measures tile value, not internal wiring.

Ask: would someone outside the tile recognize the term? If yes (public surface or tile-prescribed convention), allowed. If no (tile-internal), leaking.

## Lift

Every criterion's contribution is the delta between `with-context` and `baseline` scores. If lift is near-zero, diagnose:

1. **Coincidence with universal competence**: the tile prescribes what baseline already does (e.g., "imperative commits", "fix failing tests"). The rule codifies common practice. Retire or accept as documentation — no lift to win.
2. **Task leaked the technique**: baseline pattern-matched. Fix the task (see Bleeding), keep the criterion.
3. **Criteria grade universal competence**: testing engineering-101 rather than the tile's specific prescribed manner. Rewrite the criteria to grade the specific convention the tile teaches.

High-lift scenarios typically check specific tile-prescribed choices (a particular bot-ID discovery approach, a particular reply template, a particular CLI sequence). Keep these — do not soften them to "test reasoning" if baseline already reasons to the same outcome.

## Quality

- Every criterion `description` must explain what went wrong on failure — not just "mismatch"
- Criteria must be specific and weighted sensibly
- Weights should reflect importance, not equal distribution
- Every criterion must test something the task's output specification asks for; if the task doesn't mention it, the criteria shouldn't check for it
