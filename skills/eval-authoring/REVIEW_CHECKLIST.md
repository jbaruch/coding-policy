# Eval Scenario Review Checklist

## Task and Criteria Shape

Does the task describe a SITUATION or prescribe a TECHNIQUE?

- **Correct**: task describes what the user needs done ("Ship a hotfix", "Wire up a reviewer"). Criteria check whether the output matches the specific manner the tile prescribes — that conformance IS the tile's contribution.
- **Wrong**: task names the technique, format, sequence, or literal the criterion grades ("Ship a hotfix using `--ff-only`"). The agent passes by reading the task, not by applying the tile.

## Bleeding

Strictly: does a criterion's expected literal appear verbatim in the task description?

**Check**: for each criterion with a concrete expected value, grep the task text for that literal. A match is bleeding — baseline agents pattern-match the task and pass without the tile.

**Fix**: strip the literal from the task, keep the criterion. Baseline should still be able to attempt the situation (they'll just pick some other manner); if stripping the literal makes the task unsolvable even for a baseline, the scenario is too narrow to evaluate the tile and should be reframed.

## Leaking

Would someone outside the tile recognize the term the criterion references?

- **Public surfaces (allowed)**: `gh pr create`, REST endpoints, conventional-commits format, semver — these exist independent of the tile.
- **Tile-prescribed conventions (allowed)**: specific reply templates (`Fixed in <sha>`), chosen flags (`--ff-only`), invented format literals, specific sequences. A competent engineer without the tile would not produce these specific choices — checking for them measures tile value, not internal wiring.
- **Tile internals (leaking)**: internal skill action names, `.tessl/tiles/...` paths, tile-only identifiers that mean nothing outside the tile.

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
