---
alwaysApply: true
---

# Plugin Evals

## Coverage

- Every skill with decisional logic ships eval cases — no exceptions
- Include both positive cases (correct behavior) and negative cases (refuse bad input, produce silence when nothing actionable)
- `tessl scenario generate` skews toward happy-path scenarios — write negative cases by hand using existing scenarios as a structural template

## No Bleeding

- Fixtures must not be reachable as examples inside the skill prompt
- If the task says "use X with Y" and the criteria check "uses X" and "uses Y", that's bleeding — the eval tests reading, not problem-solving. The task describes the problem; the criteria check the solution
- Criteria values must never appear verbatim in the task description
- Tasks themselves can force bleeding: a task demanding "the exact format" of something, or a companion document whose contents reproduce skill-prescribed conventions, pushes the criteria toward reading-the-skill. If a criterion has to check a format the task demanded, both the task and the criterion need rewriting toward substance
- Audit hint: if a criterion can be satisfied by grepping the installed `skills/*/SKILL.md` files for a literal string, the criterion is testing reading — either the string is public (see No Leaking) and the test belongs in a public-convention check, or it is tile-invented and the criterion is leaking

## No Leaking

- Use sanitized or synthetic fixtures — never live user data
- Criteria must not reference tile-internal implementation details (file paths, action names, internal terms that only exist in the skill)
- Criteria **may** reference public tool/API surfaces that happen to be in the skill — e.g., `gh pr create`, `git push`, the `POST /pulls/<N>/requested_reviewers` endpoint, the `<type>(<scope>):` conventional-commits format. These exist independent of the tile and a competent engineer would choose them without reading the skill
- Criteria **may not** reference string conventions the tile invented, even when those strings look like "conventions" — e.g., a specific reply template (`Fixed in <sha>`), a specific bot-ID literal, a specific section heading the skill prescribes. If a naive agent wouldn't produce the string without having read the skill, it is leaking. Score the substance instead (the reply includes a verifiable reference; the request uses the API that supports bots; etc.)
- An eval should test observable behavior, not internal wiring

## Quality

- Failure messages must explain **what went wrong**, not just "mismatch"
- Criteria must be specific and weighted sensibly — vague criteria produce vague results
- Criteria must align with what the task actually asks for

## Persistence

- Evals run on every publish AND on a recurring cadence
- Regressions block the release — a passing eval that starts failing is a bug, not noise

## Fixture Hygiene

- Version fixtures with dates in filenames (e.g., `fixture-2025-04-17.json`)
- Update fixtures when the skill's contract changes — stale fixtures produce false passes
