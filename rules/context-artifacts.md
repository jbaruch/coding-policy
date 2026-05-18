---
alwaysApply: true
---

# Context Artifacts

## Plugin Structure

- Every tile has a `tile.json` manifest with `name`, `version`, `summary`, and `entrypoint` (→ `README.md`)
- Don't add a separate `docs` field — keep documentation in the entrypoint
- The tile's entrypoint `README.md` is the project's `README.md` — same file. Extend the existing README with rules table, skills table, and installation instructions
- Include a Tessl registry badge at the top of README: `[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2F<workspace>%2F<tile>)](https://tessl.io/registry/<workspace>/<tile>)`
- Skills live in `skills/<name>/SKILL.md`, rules live in `rules/<name>.md`
- Standard directories: `rules/`, `skills/<name>/`, `evals/` — `evals/` is omitted in tiles claiming the closed-loop carve-out in `rules/plugin-evals.md`
- Use `.tileignore` to exclude build artifacts and CI files from the published tile
- Validate structure with `tessl tile lint` before every publish
- `CHANGELOG.md` and similar repo files show as orphaned in `tessl tile lint` — lint only tracks manifest-declared paths

## Rules Are Prose

- One concept per rule file — don't combine unrelated concerns
- Compose by reference (`see rules/foo.md`), don't duplicate content across rules — if you want to state the same point in two rules, one states it and the other references it

## Rule Format

- Frontmatter: `alwaysApply: true` (no other fields needed for rules)
- H1 title matching the filename concept (e.g., `# Commit Conventions` for `commit-conventions.md`)
- No code blocks unless demonstrating a specific command
- Section count, line budget, prose discipline (what to cut, what to keep, carve-out format) — see `rules/context-writing-style.md`

## Mandatory Review

- Every skill change must pass `tessl skill review --threshold 85` before publish
- Below-threshold scores block the pipeline
- Wire into CI as a changed-skills loop, not static per-skill steps. The loop iterates over `git diff --name-only <prev-sha>..HEAD -- 'skills/'`. Reference: `.github/actions/skill-review/action.yml` (consumers `uses: jbaruch/coding-policy/.github/actions/skill-review@<ref>`)
- Fallback: review every skill when the diff base is absent (manual `workflow_dispatch`, initial push, all-zeros sentinel SHA); hard-fail when the base is set but unreachable
- Rubric verifies: frontmatter validity, execution-mode preamble matching the skill's shape (sequential workflow or action router per `rules/skill-authoring.md`), flat step numbering, typed `Skill()` calls, silence-rule compliance, channel-appropriate formatting
- Act on concrete feedback (tighter triggers, extracted reference material, tightened descriptions); re-review until the gate passes

## Disagreeing With the Reviewer

- Never lower `--threshold 85` to make a failing skill pass. Bypassing CI by other means (local publish, `[skip ci]`, disabling the review step) is forbidden under `rules/ci-safety.md`
- When you disagree with the reviewer's conclusions, run `tessl skill review --optimize <skill>` locally. Back up `SKILL.md` and any reference files before invoking
- `--optimize` is a diagnostic signal, not a patch. The reviewer's judge strips load-bearing context. Diff against the backup, keep the genuinely-improving moves (tighter triggers, less prose, better `Skill()` typing), reject over-aggressive cuts, then re-run the review and iterate
- Shipping `--optimize` output verbatim is forbidden even when the score improved. The optimizer surfaces what kinds of issues exist (actionability, progressive disclosure, redundancy); apply the signal with judgment, curate manually

## Mandatory Evals

- Every skill with decisional logic ships eval cases, subject to the closed-loop carve-out in `rules/plugin-evals.md`
- No bleeding, no leaking — full guardrails in `rules/plugin-evals.md`
- Process details live in the `eval-authoring` skill — invoke it to generate and curate scenarios

## Surface Sync

When you add, remove, or rename a rule or skill, update **all** of these:

- `tile.json` — add/remove the steering or skill entry
- CI workflow — no edit needed when the canonical changed-skills loop is in use (it picks new/removed paths up automatically via `git diff`); for bespoke workflows, confirm the path glob covers the added skill or excludes the removed one
- `README.md` — update the rules table and/or skills table
- `CHANGELOG.md` — add an entry describing the change

## Consistency Check

After modifying rules, audit for cross-rule alignment:

- No duplicated bullets across rules — if two rules say the same thing, one should reference the other
- New rules don't contradict existing ones
- Skills follow the conventions their own rules prescribe
- Documentation tables match `tile.json` entries

## Post-Edit Rule Audit

After editing a rule, audit the repo itself against the new rule text and fix any drift in the same PR:

- Grep for every instance of the pattern the rule governs (`.env.example` files, `SKILL.md` step headings, secret names, etc.) and update them to satisfy the new wording
- A rule that doesn't describe what's already committed in the repo erodes trust in every rule
- If drift can't be fixed in the same PR (e.g., it touches a frozen branch), file a follow-up issue that references the rule-edit commit
