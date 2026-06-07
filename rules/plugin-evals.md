---
alwaysApply: false
applyTo: "evals/**, skills/**/SKILL.md — when authoring or maintaining eval scenarios"
description: Eval coverage, lift-not-attainment scoring, no bleeding, no leaking, fixture hygiene
---

# Plugin Evals

## Coverage

- A scenario earns its place only by demonstrated lift on the floor model (see `Lift, Not Attainment`). Proving tile value is the goal, not coverage
- Do NOT write one scenario per prescribed behavior, pad a suite to "cover" a skill, or add a scenario you have not lift-checked. Absence is correct when nothing clears the bar
- Admission gate, not a curation afterthought: run a candidate once (baseline + with-context) before committing it; a flat result keeps it out of the suite
- Default cap of 3 scenarios per skill. Exceeding it requires justifying that each added scenario clears the lift bar AND tests a distinct tile-prescribed behavior
- The cap is a prospective admission gate on newly added scenarios; existing suites already over 3 stay governed by `Lift, Not Attainment` curation, not forced truncation
- Scope to genuine LLM-side judgment: a skill whose decisional core is a unit-tested script has no LLM-side surface to eval. Eval only the judgment the tile prescribes that the script does not make
- Negative cases only where the skill has a refusal or silence contract; write them by hand (`tessl scenario generate` skews toward happy-path)

## Task and Criteria: the load-bearing shape

- **Task** describes the SITUATION — what the user needs done. It does NOT prescribe the technique, format, sequence, or specific manner of solving it. "Ship a hotfix" is a task; "Ship a hotfix using a feature branch named `fix/*`" is a task with the answer smuggled in
- **Criteria** grade whether the output matches the specific manner this tile prescribes. Checking for tile-prescribed specifics (flag choices, format literals, sequences, conventions) is measuring tile value, not testing reading

## No Bleeding

- Primary form: a criterion value appears verbatim in the task description. Grep each criterion's expected literal against the task text — if found there, the criterion tests reading, not application
- Fix bleeding at the task, not the criterion. Strip the leaked technique/format/literal from the task; keep the criterion. If stripping makes the task unsolvable for a baseline, the scenario is too narrow — reframe it
- Second form: fixtures matching examples inside the skill prompt. Keep fixtures in a namespace separate from skill examples

## No Leaking

- Use sanitized or synthetic fixtures — never live user data. Real emails, calendar events, production PRs, or internal logs must never appear in an eval fixture; use stable synthetic IDs and scrubbed examples
- Criteria must not reference tile-internal implementation details that mean nothing outside the tile — internal skill action names, `.tessl/plugins/...` paths, tile-only identifiers
- Criteria **may** reference public tool/API surfaces that exist independent of the tile — `gh pr create`, REST endpoints, conventional-commits format, semver
- Criteria may reference tile-prescribed conventions and specific values such as reply templates like `Fixed in <sha>`, chosen flags like `--ff-only`, invented format literals. Checking for them measures application, not leaking
- Test: would someone outside the tile recognize the term? `gh pr merge` is public; `createJwtToken` internal action is tile-internal

## Lift, Not Attainment

- Lift = `with-context` score minus `baseline` score, where `with-context` is with the tile loaded and `baseline` is without. Near-zero lift on a positive case has three causes:
  1. Coincidence with universal competence — tile's prescribed manner matches what baseline agents already produce. Retire the scenario
  2. Task leaked the technique — fix the task per No Bleeding; keep the criterion
  3. Criteria grade universal competence — criteria test things baseline always does (basic git safety, obvious judgement) instead of tile-specific choices. Rewrite the criteria or retire the scenario
- Always report per-scenario lift alongside the average
- High-lift scenarios test tile-prescribed choices where baseline picks something different (bot-ID discovery, reply format, CLI sequence). Keep them — do not rewrite toward "testing reasoning" if baseline already reasons to the same outcome
- Pruning is mandatory upkeep, not optional cleanup
- Run the curation pass (see `skills/eval-curation/SKILL.md`) every few publishes
- Retire any scenario showing near-zero lift after the three-cause diagnosis and fix attempt
- Measure by per-scenario lift contribution, not raw scenario count. Most skills need 1–3 lift-bearing scenarios; many prescribed behaviors need zero, where baseline already produces the tile's manner. A small suite where every scenario pulls weight beats a large one padded with baseline-equivalents

## Quality

- Failure messages must explain **what went wrong**, not just "mismatch"
- Criteria must be specific and weighted sensibly — vague criteria produce vague results
- Criteria must align with what the task actually asks for

## Persistence

- Tessl's publish pipeline runs the eval suite automatically when `tessl tile publish` (or `tesslio/patch-version-publish`) executes — that is the persistence point
- Do not add a `tessl eval run` step to tile-repo CI; do not add a scheduled or recurring workflow that re-runs the eval suite
- Out of scope: local invocations during authoring/debugging, and ad-hoc invocations by separate measurement rigs (e.g., `jbaruch/coding-policy-evals`)
- Regressions block the publish
- Fix the regression, or fix the scenario when the cause is fixture drift per `Fixture Hygiene`
- Do not add a parallel CI step that could mask the publish-layer failure

## Naming

- Scenario directory names use **kebab-case** — lowercase + hyphens only (`my-scenario`, not `MyScenario`, `my_scenario`, or `my scenario`)
- Skill-specific scenarios: prefix with the skill name — `<skill>-<descriptor>` (e.g., `install-reviewer-refuses-overwrite`, `eval-curation-task-leak-fix`)
- Cross-cutting scenarios: name the behavior directly without a skill prefix (e.g., `pr-merge-and-post-merge-cleanup`)
- Descriptors name the behavior under test, not the implementation: `refuses-overwrite` ✓, `checks-existing-file-via-stat` ✗
- Default cap at 40 characters for the directory name — driven by tessl's interactive tooling (`tessl eval view`, `tessl scenario generate`) silently truncating longer names
- Cap applies prospectively; scenarios already run through `tessl eval run` that exceed it are grandfathered by the rename-stability clause (which wins over the cap)
- Once committed and run through `tessl eval run`, the name is stable — do not rename. `tessl eval view` identifies scenarios by directory name; renaming resets the lift history the Persistence section relies on
- Rig conventions (programmatic shapes like `<rule>-<fixture_type>-<cell>-run-<n>`) may diverge from kebab-case-with-descriptor when documented in the tile's `evals/instructions.json`
- Narrow exception for rig-shaped scenarios that exceed the 40-char default cap
- Preconditions (all required):
  1. Rig's programmatic shape provably cannot fit 40 chars without losing reconstructability of its components (e.g., 4-tuple `rule`/`fixture_type`/`cell`/`run` needed by the rig's scorer for lift bucketing)
  2. `evals/instructions.json` declares the rig's actual safe length AND names every tessl-eval tool the rig touches end-to-end
  3. Rig bypasses the interactive tools that drive the default cap — scenario names built by custom script, runs invoked via `tessl eval run <path>`, scoring via custom scorer
  4. Rig owns naming-collision and truncation-driven scoring drift in its own scorer, not in tessl's tooling
  5. Rig's CHANGELOG records the carve-out under a `### Rules` (or equivalent) entry citing this clause
- Rename-stability applies regardless of cap — `tessl eval view` identifies scenarios by directory name irrespective of how the name was generated
- Every scenario outside an active rig carve-out still respects the 40-char default

## Fixture Hygiene

- Version fixtures with dates in filenames (e.g., `fixture-2025-04-17.json`)
- Update fixtures when the skill's contract changes — stale fixtures produce false passes
