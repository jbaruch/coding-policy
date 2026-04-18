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

## No Leaking

- Use sanitized or synthetic fixtures — never live user data
- Criteria must not reference tile-internal implementation details (file paths, action names, internal terms that only exist in the skill)
- Criteria **may** test for skill-prescribed approaches when those approaches use public tools and APIs (e.g., specific CLI commands, public endpoints) — that's testing whether the skill's guidance was followed, not leaking internals
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
