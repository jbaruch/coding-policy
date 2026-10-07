---
alwaysApply: false
applyTo: ".tessl-plugin/plugin.json, agent-plugin.yaml, rules/**, skills/**, .tesslignore, CHANGELOG.md, README.md — when authoring or modifying plugin artifacts; the Tessl manifest, lint, review and publication provisions bind Tessl plugin artifacts only"
description: Plugin structure, rule/skill format, review pipeline, surface sync, consistency audits — the authoring contract for plugin artifacts, Tessl-specific provisions scoped to Tessl distribution
---

# Context Artifacts

## Scope

- Governs plugin context artifacts in every distribution — rule files, `SKILL.md` files, the README rules and skills tables, `CHANGELOG.md`
- Distributed through Tessl: the artifact reaches consumers via `tessl install`, evidenced by a Tessl plugin manifest (`.tessl-plugin/plugin.json` or legacy `tile.json`) or a publish path calling `tessl plugin publish` or `tesslio/patch-version-publish`
- Tessl-bound sections — Plugin Structure, Mandatory Review, Credit-Outage Review Carve-Out, Disagreeing With the Reviewer — bind an artifact distributed through Tessl
- Distribution-independent sections — Artifact Layout, Rules Are Prose, Rule Format, Surface Sync, Consistency Check, Post-Edit Rule Audit — bind every plugin artifact, whatever publishes it
- CHANGELOG Hygiene's publish-on-merge, stamp-step and `tesslio/patch-version-publish` bullets are Tessl-bound
- Every other CHANGELOG Hygiene bullet is distribution-independent
- An artifact published through another channel — a GitHub-tag-published ACR package — owes the distribution-independent sections plus `rules/skill-authoring.md`, `rules/testing-standards.md` and `rules/language-diagnostics.md` in full
- It owes `rules/ci-safety.md` too, confirming each publication independently against the channel that carried it, mixed distribution included — see `skills/release/references/release-contract.md` Publication Confirmation

## Artifact Layout

- Skills live in `skills/<name>/SKILL.md`, rules live in `rules/<name>.md`
- Standard directories: `rules/`, `skills/<name>/`
- The plugin's `README.md` is the project's `README.md` — same file. Extend the existing README with rules table, skills table, and installation instructions

## Plugin Structure

- Every Tessl plugin has a `.tessl-plugin/plugin.json` manifest with `name`, `version`, and `description` — full schema in `rules/skill-authoring.md`
- Include a Tessl registry badge at the top of README: `[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2F<workspace>%2F<plugin>)](https://tessl.io/registry/<workspace>/<plugin>)`
- Use `.tesslignore` to exclude build artifacts and CI files from the published plugin
- Validate structure with `tessl plugin lint` before every publish
- `CHANGELOG.md` and similar repo files show as orphaned in `tessl plugin lint` — lint only tracks manifest-declared paths

## Rules Are Prose

- One concept per rule file — don't combine unrelated concerns
- Compose by reference (`see rules/foo.md`), don't duplicate content across rules — if you want to state the same point in two rules, one states it and the other references it
- Text governing one skill's workflow lives in that skill's `references/`, with a binding pointer from the rule
- A moved carve-out follows `rules/context-writing-style.md` Structure
- The coding-policy plugin's own `rules/` total stays within the byte budget `scripts/check-rules-budget.sh` owns (top-of-file constant)

## Rule Format

- H1 title matching the filename concept (e.g., `# Commit Conventions` for `commit-conventions.md`)
- No code blocks unless demonstrating a specific command
- Frontmatter fields, passthrough behavior, and scoping with `applyTo:` — see `rules/rule-frontmatter.md`
- Section count, line budget, prose discipline (what to cut, what to keep, carve-out format) — see `rules/context-writing-style.md`

## Mandatory Review

- Every skill change in a Tessl-distributed plugin must pass `tessl review run --threshold 85` before publish
- Below-threshold scores block the pipeline
- Mixed distribution keeps the gate — a plugin that also ships through another channel still reviews every changed skill before its Tessl publish
- Deleting the Tessl manifest exempts nothing while the content still publishes through Tessl
- Adding another channel's manifest exempts nothing either
- A skill distributed through no Tessl path owes `rules/skill-authoring.md` in full, its repo's CI gates, and its external policy and Copilot review — never this command
- CI wiring and the review rubric are binding: read `skills/release/references/skill-review-gate.md` CI Wiring before wiring or changing the review step
- Act on concrete feedback (tighter triggers, extracted reference material, tightened descriptions); re-review until the gate passes
- Credit-outage tolerance is opt-in and narrow — see Credit-Outage Review Carve-Out

## Credit-Outage Review Carve-Out

- Narrow exception for skipping review during a tessl out-of-credits (403) billing outage
- Applies when `tessl review run` fails with the out-of-credits signature; never a below-threshold score
- Preconditions are binding: read `skills/release/references/skill-review-gate.md` Credit-Outage Review Carve-Out before relying on it
- Every skill reviewed clean, and every non-credit failure, still blocks or passes exactly as Mandatory Review prescribes

## Disagreeing With the Reviewer

- Never lower `--threshold 85` to make a failing skill pass. Bypassing CI by other means (local publish, `[skip ci]`, disabling the review step) is forbidden under `rules/ci-safety.md`
- When you disagree with the reviewer's conclusions, run the fix loop per `skills/release/references/skill-review-gate.md` Fix Loop, a binding read before invoking it
- Shipping the fix loop's output verbatim is forbidden even when the score improved

## Surface Sync

When you add, remove, or rename a rule or skill, update **all** of these:

- The plugin manifest, `.tessl-plugin/plugin.json` for a Tessl plugin — add/remove the rule path in `rules` or the skill path in `skills`
- CI workflow — no edit needed when the canonical changed-skills loop is in use (it picks new/removed paths up automatically via `git diff`); for bespoke workflows, confirm the path glob covers the added skill or excludes the removed one
- `README.md` — update the rules table and/or skills table
- `CHANGELOG.md` — add an entry describing the change

## CHANGELOG Hygiene

- Every merge publishes a version (publish-on-merge, e.g. via `tesslio/patch-version-publish`)
- No `Unreleased` section — the heading is forbidden
- `tesslio/patch-version-publish` does NOT stamp `CHANGELOG.md` (it only bumps the manifest version and publishes)
- Stamping a `## <version> — <date>` heading is a separate step the repo wires itself
- Reusable stamp step: `.github/actions/stamp-changelog` (consumers `uses: jbaruch/coding-policy/.github/actions/stamp-changelog@<ref>`, wired before `tesslio/patch-version-publish`)
- **With a wired stamp step:** authors add un-headed `### ` entry blocks at the top of `CHANGELOG.md`
- The stamp step writes the `## <version> — <date>` heading above those blocks before publish
- **Without a stamp step:** authors write the `## <version> — <date>` heading manually above their entries
- Never leave un-headed `### ` blocks expecting auto-stamping when no stamp step is wired
- Consolidation groups related entries, collapses redundant detail, retains load-bearing facts (what changed, references)
- Entry length and archive discipline follow `rules/context-writing-style.md`, never a per-entry sentence cap
- Audit the top section for duplication — multiple PRs reworking the same rule become one entry with the final outcome

## Consistency Check

After modifying rules, audit for cross-rule alignment:

- No duplicated bullets across rules — if two rules say the same thing, one should reference the other
- Don't duplicate long command literals or contract statements between rules and skills
- Rules state the contract
- Skills (or their scripts) carry the executable form per `rules/script-delegation.md`
- New rules don't contradict existing ones
- Skills follow the conventions their own rules prescribe
- Documentation tables match the plugin manifest's entries, `.tessl-plugin/plugin.json` for a Tessl plugin

## Post-Edit Rule Audit

After editing a rule, audit the repo itself against the new rule text and fix any drift in the same PR:

- Grep for every instance of the pattern the rule governs (`.env.example` files, `SKILL.md` step headings, secret names, etc.) and update them to satisfy the new wording
- A rule that doesn't describe what's already committed in the repo erodes trust in every rule
- If drift can't be fixed in the same PR, record it with the rule-edit commit under `rules/boy-scout.md` How to Apply
