---
alwaysApply: true
---

# Plugin Evals

## Coverage

- Every skill with decisional logic ships eval cases, subject only to the closed-loop carve-out below
- Include both positive cases (correct behavior) and negative cases (refuse bad input, produce silence when nothing actionable)
- Write negative cases by hand using existing scenarios as a structural template (`tessl scenario generate` skews toward happy-path)

## Closed-Loop Carve-Out

- Narrow exception for closed-loop automated systems with no human eval-result consumption — tile exempt from the Coverage clause above and the entire Persistence section
- Preconditions (all required):
  1. No human review — no human reads eval output in any form: scores, lift deltas, scenario diffs, regression alerts, failure traces, dashboards, periodic reports
  2. No gating use — eval results do not gate any downstream automated action: release blocks, deploy blocks, publish-tile gates, rollback triggers, alert routing, dashboard surfaces, paging, summary stats consumed by another workflow
  3. Affirmative owner declaration — tile's CHANGELOG records the exception under a `### Rules` entry naming this rule + date
- "We plan to look at results" does NOT qualify
- "We have a gate that fails on regressions but nobody checks the failures" does NOT qualify (precondition 2)
- Re-introducing any consumption later (human review or automated gating) requires re-introducing evals first under the standard requirement
- Every other tile follows the rule in full

## Task and Criteria: the load-bearing shape

- **Task** describes the SITUATION — what the user needs done. It does NOT prescribe the technique, format, sequence, or specific manner of solving it. "Ship a hotfix" is a task; "Ship a hotfix using a feature branch named `fix/*`" is a task with the answer smuggled in
- **Criteria** grade whether the output matches the specific manner this tile prescribes. Checking for tile-prescribed specifics (flag choices, format literals, sequences, conventions) is measuring tile value, not testing reading

## No Bleeding

- Primary form: a criterion value appears verbatim in the task description. Grep each criterion's expected literal against the task text — if found there, the criterion tests reading, not application
- Fix bleeding at the task, not the criterion. Strip the leaked technique/format/literal from the task; keep the criterion. If stripping makes the task unsolvable for a baseline, the scenario is too narrow — reframe it
- Second form: fixtures matching examples inside the skill prompt. Keep fixtures in a namespace separate from skill examples

## No Leaking

- Use sanitized or synthetic fixtures — never live user data. Real emails, calendar events, production PRs, or internal logs must never appear in an eval fixture; use stable synthetic IDs and scrubbed examples
- Criteria must not reference tile-internal implementation details that mean nothing outside the tile — internal skill action names, `.tessl/tiles/...` paths, tile-only identifiers
- Criteria **may** reference public tool/API surfaces that exist independent of the tile — `gh pr create`, REST endpoints, conventional-commits format, semver
- Criteria may reference tile-prescribed conventions and specific values — reply templates (`Fixed in <sha>`), chosen flags (`--ff-only`), invented format literals. Checking for them measures application, not leaking
- Test: would someone outside the tile recognize the term? `gh pr merge` is public; `createJwtToken` internal action is tile-internal

## Lift, Not Attainment

- Lift = `with-context` score (tile loaded) minus `baseline` score (tile not loaded). Near-zero lift on a positive case has three causes:
  1. Coincidence with universal competence — tile's prescribed manner matches what baseline agents already produce. Retire the scenario
  2. Task leaked the technique — fix the task per No Bleeding; keep the criterion
  3. Criteria grade universal competence — criteria test things baseline always does (basic git safety, obvious judgement) instead of tile-specific choices. Rewrite the criteria or retire the scenario
- Always report per-scenario lift alongside the average
- High-lift scenarios test tile-prescribed choices where baseline picks something different (bot-ID discovery, reply format, CLI sequence). Keep them — do not rewrite toward "testing reasoning" if baseline already reasons to the same outcome
- Pruning is mandatory upkeep, not optional cleanup. Run the curation pass (see `skills/eval-curation/SKILL.md`) every few publishes; retire any scenario showing near-zero lift after the three-cause diagnosis and fix attempt
- Measure by per-scenario lift contribution, not raw scenario count — a 10-scenario suite where every scenario pulls weight beats a 35-scenario suite where half score baseline-equivalent

## Quality

- Failure messages must explain **what went wrong**, not just "mismatch"
- Criteria must be specific and weighted sensibly — vague criteria produce vague results
- Criteria must align with what the task actually asks for

## Persistence

- Tessl's publish pipeline runs the eval suite automatically when `tessl tile publish` (or `tesslio/patch-version-publish`) executes — that is the persistence point
- Do not add a `tessl eval run` step to tile-repo CI; do not add a scheduled or recurring workflow
- Out of scope: local invocations during authoring/debugging, and ad-hoc invocations by separate measurement rigs (e.g., `jbaruch/coding-policy-evals`)
- Regressions block the publish — fix the regression (or the scenario if it's fixture drift per `Fixture Hygiene`); do not add a parallel CI step that could mask the publish-layer failure

## Naming

- Scenario directory names use **kebab-case** — lowercase + hyphens only (`my-scenario`, not `MyScenario`, `my_scenario`, or `my scenario`)
- Skill-specific scenarios: prefix with the skill name — `<skill>-<descriptor>` (e.g., `install-reviewer-refuses-overwrite`, `eval-curation-task-leak-fix`)
- Cross-cutting scenarios: name the behavior directly without a skill prefix (e.g., `pr-merge-and-post-merge-cleanup`)
- Descriptors name the behavior under test, not the implementation: `refuses-overwrite` ✓, `checks-existing-file-via-stat` ✗
- Hard cap at 40 characters for the directory name — some tooling in the tessl-eval pipeline silently truncates longer names
- Cap applies prospectively; scenarios already run through `tessl eval run` that exceed the cap are grandfathered by the rename-stability clause (which wins over the cap)
- Once committed and run through `tessl eval run`, the name is stable — do not rename. `tessl eval view` identifies scenarios by directory name; renaming resets the lift history the Persistence section relies on
- Rig conventions (programmatic shapes like `<rule>-<fixture_type>-<cell>-run-<n>`) may diverge when documented in the tile's `evals/instructions.json`. The 40-char cap and rename-stability still apply

## Fixture Hygiene

- Version fixtures with dates in filenames (e.g., `fixture-2025-04-17.json`)
- Update fixtures when the skill's contract changes — stale fixtures produce false passes
