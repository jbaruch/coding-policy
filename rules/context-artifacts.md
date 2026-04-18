---
alwaysApply: true
---

# Context Artifacts

## Plugin Structure

- Every tile has a `tile.json` manifest with `name`, `version`, `summary`, and `entrypoint` (→ `README.md`). Don't add a separate `docs` field — keep all documentation in the entrypoint to avoid duplicate tables that drift out of sync
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
