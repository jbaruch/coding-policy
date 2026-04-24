---
alwaysApply: true
---

# Plugin Evals

## Coverage

- Every skill with decisional logic ships eval cases — no exceptions
- Include both positive cases (correct behavior) and negative cases (refuse bad input, produce silence when nothing actionable)
- `tessl scenario generate` skews toward happy-path scenarios — write negative cases by hand using existing scenarios as a structural template

## Task and Criteria: the load-bearing shape

- **Task** describes the SITUATION — what the user needs done. It does NOT prescribe the technique, format, sequence, or specific manner of solving it. "Ship a hotfix" is a task; "Ship a hotfix using a feature branch named `fix/*`" is a task with the answer smuggled in
- **Criteria** grade whether the output matches the specific manner this tile prescribes. That conformance IS the tile's contribution — without the tile, agents pick some manner; with the tile, they pick the manner the tile teaches. Checking for tile-prescribed specifics (flag choices, format literals, sequences, conventions) is measuring tile value, not testing reading

## No Bleeding

- Bleeding is strictly: a criterion value appearing verbatim in the task description. Grep each criterion's expected literal against the task text — if you find it there, the criterion is testing reading of the task, not application of the tile
- Fix bleeding at the task, not at the criterion. Strip the technique/format/literal from the task; keep the criterion checking for the tile-prescribed answer. Baseline agents should be able to attempt the SITUATION described in the task (they'll just pick some other manner); if stripping the leak makes the task unsolvable even for a baseline, the scenario is too narrow to evaluate the tile and should be reframed
- Fixtures must not be reachable as examples inside the skill prompt — criteria check solutions the skill taught in general, not instances the skill used as teaching examples

## No Leaking

- Use sanitized or synthetic fixtures — never live user data. Real emails, calendar events, production PRs, or internal logs must never appear in an eval fixture; use stable synthetic IDs and scrubbed examples
- Criteria must not reference tile-internal implementation details that mean nothing outside the tile — internal skill action names, `.tessl/tiles/...` paths, tile-only identifiers
- Criteria **may** reference public tool/API surfaces that exist independent of the tile — `gh pr create`, REST endpoints, conventional-commits format, semver
- Criteria **may** reference tile-prescribed conventions and specific values — reply templates (`Fixed in <sha>`), chosen flags (`--ff-only`), specific sequences, invented format literals. A competent engineer without the tile would not produce those specific choices; that is precisely why they measure tile value. Checking for them is measuring application, not leaking
- The distinction between a public surface and a tile-internal is whether someone outside the tile would recognize the term at all. "Uses `gh pr merge`" is public. "Uses `createJwtToken` internal action" is tile-internal

## Lift, Not Attainment

- Every scenario's value is measured as **lift** — the delta between the `with-context` score (tile loaded) and the `baseline` score (tile not loaded). A scenario with near-zero lift on a positive case is telling you one of three things:
  1. **Coincidence with universal competence**: the tile's prescribed manner matches what baseline agents already produce by default (e.g. a rule saying "use imperative mood in commits" when agents already do that). The rule codifies common practice; lift won't show because output is the same. Retire or accept as documentation
  2. **Task leaked the technique**: baseline pattern-matched its way to the criterion because the task mentioned the technique. Fix the task per No Bleeding above — do NOT drop the criterion
  3. **Criteria grade universal competence**: the criteria test things baseline always does (basic git safety, obvious engineering judgement) rather than tile-specific choices. Rewrite the criteria to test the specific manner the tile prescribes, or retire the scenario
- Aggregate attainment on its own is a vanity metric. A tile averaging 95% attainment with 82% baseline is contributing 13 points of real value, not 95. Always report per-scenario lift alongside the average
- High-lift scenarios typically test specific tile-prescribed choices where baseline would pick something different (a specific bot-ID discovery approach, a specific reply format, a specific CLI sequence). These are legitimate and should be kept — do not rewrite them toward "testing reasoning" if baseline already reasons to the same outcome

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
