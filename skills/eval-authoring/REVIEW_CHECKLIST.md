# Eval Scenario Review Checklist

## Null-Test (Universal-Competence Trap)

Would a competent baseline agent — with the tile uninstalled — already pass this criterion?

If yes, the criterion is a null test: it grades universal competence, not the tile's contribution. The symptom at eval-run time is baseline scores near ceiling (≥ 90% on a positive case) and lift ≈ 0 despite with-context looking high.

Two common causes:

1. **The task names the technique or format** (e.g., "use the weighted_checklist format", "deploys via the approval flag", "follow these conventions"). Baseline agents pattern-match the task and produce exactly what the criterion grades.
2. **The criteria grade generally-known behaviour** (e.g., "refuses to merge red CI", "uses `git checkout -b`", "fixes the failing test properly"). Universal engineering judgement clears the bar regardless of the tile.

**Fix**: rewrite the task to state the *situation* without the technique, and strengthen the criterion so it requires tile-specific reasoning to pass. If the criterion has no tile-specific content left after that rewrite, retire the scenario — a scenario that measures nothing inflates aggregate attainment and hides real tile value.

**Run the thought experiment before committing a scenario**: imagine the tile uninstalled. If a competent off-the-shelf agent would pass the criterion by default, do not commit the criterion as written.

## Bleeding

Does the task hand the agent the answer?

If the task says "use library X with algorithm Y" and the criteria check "uses library X" and "uses algorithm Y", that's bleeding — the eval tests reading comprehension, not problem-solving. The task should describe the problem; the criteria should check the solution.

Tasks themselves can force bleeding too: a task demanding "the exact format" of something, or a companion document whose contents reproduce skill-prescribed conventions, pushes the criteria toward reading-the-skill. Fix both when you find this.

**Check 1 (task overlap)**: for each criterion, search the task text for the criterion's expected value. If found verbatim, it's bleeding.

**Check 2 (skill grep)**: for each criterion, grep the skill files the runtime loads for the evaluated agent — the in-tree `skills/*/SKILL.md` when this tile is the subject, or `.tessl/tiles/<workspace>/<tile>/skills/*/SKILL.md` when the tile is installed as a dependency. A grep hit is a mechanical audit hint, not an automatic failure — classify it using the Leaking section below: if the string is a public tool/API surface, the criterion is fine; if it is a tile-invented convention, the criterion is leaking and needs rewriting.

## Leaking

Does the task or criteria reference tile internals?

- File paths, action names, internal terms that only exist in the skill
- Criteria **may** reference public tool/API surfaces that happen to be in the skill — `gh pr create`, `git push`, REST endpoints like `POST /repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers`, conventional-commits title format. These exist independent of the tile
- Criteria **may not** reference string conventions the tile invented, even when those look like "conventions" — specific reply templates, bot-ID literals, prescribed section headings. Score the substance (the reply names a verifiable reference; the request uses the API that supports bots) not the literal

**Check**: for each criterion, ask "would a competent engineer produce this without having read the skill?" If not, it's leaking.

## Quality

- Every criterion `description` must explain what went wrong on failure — not just "mismatch"
- Criteria must be specific and weighted sensibly
- Weights should reflect importance to the task, not equal distribution

## Consistency

- Every criterion must test something the task's output specification asks for
- If the task doesn't mention it, the criteria shouldn't check for it
