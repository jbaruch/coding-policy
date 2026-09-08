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
- Distribution-independent sections — Rules Are Prose, Rule Format, Surface Sync, Consistency Check, Post-Edit Rule Audit — bind every plugin artifact, whatever publishes it
- CHANGELOG Hygiene's publish-on-merge, stamp-step and `tesslio/patch-version-publish` bullets are Tessl-bound; its consolidation and duplication bullets are not
- An artifact published through another channel — a GitHub-tag-published ACR package — owes the distribution-independent sections plus `rules/skill-authoring.md`, `rules/testing-standards.md` and `rules/language-diagnostics.md` in full
- It owes `rules/ci-safety.md` too, reading that rule's registry-advance and moderation conjuncts against its own channel's published-artifact evidence — see `rules/ci-safety.md` Always Watch CI

## Plugin Structure

- Every Tessl plugin has a `.tessl-plugin/plugin.json` manifest with `name`, `version`, and `description` — full schema in `rules/skill-authoring.md`
- The plugin's `README.md` is the project's `README.md` — same file. Extend the existing README with rules table, skills table, and installation instructions
- Include a Tessl registry badge at the top of README: `[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2F<workspace>%2F<plugin>)](https://tessl.io/registry/<workspace>/<plugin>)`
- Skills live in `skills/<name>/SKILL.md`, rules live in `rules/<name>.md`
- Standard directories: `rules/`, `skills/<name>/`
- Use `.tesslignore` to exclude build artifacts and CI files from the published plugin
- Validate structure with `tessl plugin lint` before every publish
- `CHANGELOG.md` and similar repo files show as orphaned in `tessl plugin lint` — lint only tracks manifest-declared paths

## Rules Are Prose

- One concept per rule file — don't combine unrelated concerns
- Compose by reference (`see rules/foo.md`), don't duplicate content across rules — if you want to state the same point in two rules, one states it and the other references it

## Rule Format

- H1 title matching the filename concept (e.g., `# Commit Conventions` for `commit-conventions.md`)
- No code blocks unless demonstrating a specific command
- Frontmatter fields, passthrough behavior, and scoping with `applyTo:` — see `rules/rule-frontmatter.md`
- Section count, line budget, prose discipline (what to cut, what to keep, carve-out format) — see `rules/context-writing-style.md`

## Mandatory Review

- Every skill change in a Tessl-distributed plugin must pass `tessl review run --threshold 85` before publish
- Below-threshold scores block the pipeline
- Mixed distribution keeps the gate — a plugin that also ships through another channel still reviews every changed skill before its Tessl publish
- Deleting the Tessl manifest exempts nothing while the content still publishes through Tessl; adding another channel's manifest exempts nothing either
- A skill distributed through no Tessl path owes `rules/skill-authoring.md` in full, its repo's CI gates, and its external policy and Copilot review — never this command
- Wire into CI as a changed-skills loop, not static per-skill steps. The loop iterates over `git diff --name-only <prev-sha>..HEAD -- 'skills/'`. Reference: `.github/actions/skill-review/action.yml` (consumers `uses: jbaruch/coding-policy/.github/actions/skill-review@<ref>`)
- Fallback: review every skill when the diff base is absent (manual `workflow_dispatch`, initial push, all-zeros sentinel SHA); hard-fail when the base is set but unreachable
- Rubric verifies: frontmatter validity, execution-mode preamble matching the skill's shape (sequential workflow or action router per `rules/skill-authoring.md`), flat step numbering, typed `Skill()` calls, silence-rule compliance, channel-appropriate formatting
- Act on concrete feedback (tighter triggers, extracted reference material, tightened descriptions); re-review until the gate passes
- Credit-outage tolerance is opt-in and narrow — see Credit-Outage Review Carve-Out

## Credit-Outage Review Carve-Out

- Narrow exception for skipping review during a tessl out-of-credits (403) billing outage
- Applies when `tessl review run` fails with the out-of-credits signature; never a below-threshold score
- The `skill-review` action's `credit-outage: skip` input publishes the affected skill unreviewed rather than blocking every skill-changing merge until credits return
- Preconditions (all required):
  1. Opt-in explicit — the consumer sets `credit-outage: skip`; default `fail` preserves the gate. Classification is the action's decision contract — see `.github/actions/skill-review/review-skills.sh` `run_reviews`
  2. Fail-safe — only the out-of-credits signature skips; every other non-zero exit still hard-fails the publish (below-threshold score, auth error, tooling bug)
  3. Each unreviewed publish is flagged — a `::warning::` plus a `$GITHUB_STEP_SUMMARY` note names every skipped skill, surfaced on the `unreviewed-skills` action output
  4. The gate self-heals — every publish re-attempts review; a credit top-up or the monthly reset restores it
- The forbidden review-step bypass in Disagreeing With the Reviewer remains forbidden
- Every skill reviewed clean, and every non-credit failure, still blocks or passes exactly as Mandatory Review prescribes

## Disagreeing With the Reviewer

- Never lower `--threshold 85` to make a failing skill pass. Bypassing CI by other means (local publish, `[skip ci]`, disabling the review step) is forbidden under `rules/ci-safety.md`
- When you disagree with the reviewer's conclusions, run `tessl review fix <skill>` locally
- Back up `SKILL.md` and any reference files before invoking the fix loop
- The fix loop is a diagnostic signal, not a patch
- Diff the applied changes against the backup
- Keep the genuinely-improving moves (tighter triggers, less prose, better `Skill()` typing)
- Reject the over-aggressive cuts
- Re-run `tessl review run` until the gate passes
- Shipping the fix loop's output verbatim is forbidden even when the score improved
- Curate the fixes manually with judgment

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
- If drift can't be fixed in the same PR (e.g., it touches a frozen branch), file a follow-up issue that references the rule-edit commit
