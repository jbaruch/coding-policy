---
alwaysApply: true
---

# Context Artifacts

## Plugin Structure

- Every tile has a `tile.json` manifest with `name`, `version`, `summary`, and `entrypoint` (→ `README.md`). Don't add a separate `docs` field — keep all documentation in the entrypoint to avoid duplicate tables that drift out of sync
- The tile's entrypoint `README.md` **is** the project's `README.md` — they are the same file. Do not create a separate README for the tile. If the project already has a README, extend it with tile content (rules table, skills table, installation instructions)
- Include a Tessl registry badge at the top of README: `[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2F<workspace>%2F<tile>)](https://tessl.io/registry/<workspace>/<tile>)`
- Skills live in `skills/<name>/SKILL.md`, rules live in `rules/<name>.md`
- Standard directories: `rules/`, `skills/<name>/`, `evals/`
- Use `.tileignore` to exclude build artifacts and CI files from the published tile
- Validate structure with `tessl tile lint` before every publish
- Standard repo files like `CHANGELOG.md` will show as orphaned in lint — this is expected; lint only tracks manifest-declared paths

## Context Artifacts Are First-Class

- Skills, rules, and scripts are first-class deliverables packaged as a tile
- They share the same lifecycle guarantees as code: versioned, reviewed, tested
- Not ad-hoc files — they ship with the plugin and follow its release process

## Rules Are Prose

- One concept per rule file — don't combine unrelated concerns
- Include both **why** and **how** — the "why" prevents workarounds
- Compose by reference (`see rules/foo.md`), don't duplicate content across rules — if you want to state the same point in two rules, one states it and the other references it

## Rule Format

- Frontmatter: `alwaysApply: true` (no other fields needed for rules)
- H1 title matching the filename concept (e.g., `# Commit Conventions` for `commit-conventions.md`)
- H2 sections grouping related bullets — aim for 3–6 sections per rule
- Concise bullets, ~25–40 lines total — if a rule exceeds this, it's covering more than one concept
- No code blocks unless demonstrating a specific command — rules are prose, not scripts

## Mandatory Review

- Every skill change must pass `tessl skill review --threshold 85` before publish
- Below-threshold scores block the pipeline — no exceptions
- When adding a skill, add a `tessl skill review --threshold 85 skills/<name>` step to the CI workflow — the review gate is only real if CI enforces it
- The review rubric verifies frontmatter validity, an execution-mode preamble appropriate to the skill's shape (sequential-workflow preamble for in-order skills, action-router preamble for skills where the agent picks one of several alternatives by user intent — both forms specified in `rules/skill-authoring.md`), flat step numbering, typed `Skill()` calls (no prose invocations), silence-rule compliance, and channel-appropriate formatting (e.g., no Markdown in HTML-only channels)
- Read the reviewer's suggestions — the review tool is a development aid, not just a gate. Act on concrete feedback (improve trigger terms, extract reference material, tighten descriptions) and re-review until you've addressed the actionable suggestions

## Mandatory Evals

- Every skill with decisional logic ships eval cases
- No bleeding, no leaking — full guardrails in `rules/plugin-evals.md`
- Process details live in the `eval-authoring` skill — invoke it to generate and curate scenarios

## Surface Sync

When you add, remove, or rename a rule or skill, update **all** of these:

- `tile.json` — add/remove the steering or skill entry
- CI workflow — add/remove the `tessl skill review` step for each skill
- `README.md` — update the rules table and/or skills table
- `CHANGELOG.md` — add an entry describing the change

## Consistency Check

After modifying rules, audit for cross-rule alignment:

- No duplicated bullets across rules — if two rules say the same thing, one should reference the other
- New rules don't contradict existing ones
- Skills follow the conventions their own rules prescribe
- Documentation tables match `tile.json` entries exactly

## Post-Edit Rule Audit

After editing a rule, audit the repo itself against the new rule text and fix any drift in the same PR:

- Grep for every instance of the pattern the rule governs (`.env.example` files, `SKILL.md` step headings, secret names, etc.) and update them to satisfy the new wording
- A rule that doesn't describe what's already committed in the repo erodes trust in every rule
- If drift can't be fixed in the same PR (e.g., because it touches a frozen branch), file a follow-up issue that references the rule-edit commit
