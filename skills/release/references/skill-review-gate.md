# Skill Review Gate

Detail of the `rules/context-artifacts.md` skill-review gate, moved out of the always-loaded rule (#642). It binds as rule content: the rule keeps the gate's directives and each carve-out's trigger line, and requires reading this file before relying on them. Text is unchanged from the rule.

## Credit-Outage Review Carve-Out

- Narrow exception for skipping review during a tessl out-of-credits (403) billing outage
- Applies when `tessl review run` fails with the out-of-credits signature; never a below-threshold score
- The `skill-review` action's `credit-outage: skip` input publishes the affected skill unreviewed rather than blocking every skill-changing merge until credits return
- Preconditions (all required):
  1. Opt-in explicit — the consumer sets `credit-outage: skip`; default `fail` preserves the gate. Classification is the action's decision contract — see `.github/actions/skill-review/review-skills.sh` `run_reviews`
  2. Fail-safe — only the out-of-credits signature skips; every other non-zero exit still hard-fails the publish (below-threshold score, auth error, tooling bug)
  3. Each unreviewed publish is flagged — a `::warning::` plus a `$GITHUB_STEP_SUMMARY` note names every skipped skill, surfaced on the `unreviewed-skills` action output
  4. The gate self-heals — every publish re-attempts review; a credit top-up or the monthly reset restores it
- The forbidden review-step bypass in `rules/context-artifacts.md` Disagreeing With the Reviewer remains forbidden
- Every skill reviewed clean, and every non-credit failure, still blocks or passes exactly as Mandatory Review prescribes

## CI Wiring

- Wire into CI as a changed-skills loop, not static per-skill steps. The loop iterates over `git diff --name-only <prev-sha>..HEAD -- 'skills/'`. Reference: `.github/actions/skill-review/action.yml` (consumers `uses: jbaruch/coding-policy/.github/actions/skill-review@<ref>`)
- Fallback: review every skill when the diff base is absent (manual `workflow_dispatch`, initial push, all-zeros sentinel SHA); hard-fail when the base is set but unreachable
- Rubric verifies: frontmatter validity, execution-mode preamble matching the skill's shape (sequential workflow or action router per `rules/skill-authoring.md`), flat step numbering, typed `Skill()` calls, silence-rule compliance, channel-appropriate formatting

## Fix Loop

- When you disagree with the reviewer's conclusions, run `tessl review fix <skill>` locally
- Back up `SKILL.md` and any reference files before invoking the fix loop
- The fix loop is a diagnostic signal, not a patch
- Diff the applied changes against the backup
- Keep the genuinely-improving moves (tighter triggers, less prose, better `Skill()` typing)
- Reject the over-aggressive cuts
- Re-run `tessl review run` until the gate passes
- Shipping the fix loop's output verbatim is forbidden even when the score improved
- Curate the fixes manually with judgment
