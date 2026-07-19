# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Codex policy reviewer (native Codex code-review app)

The policy reviewer is the OpenAI Codex code-review GitHub App, running on a ChatGPT subscription (no API key). It is steered by the repo's `AGENTS.md ## Review guidelines`, which point it at the in-tree `rules/*.md`.

- **Trigger:** with **Automatic reviews** enabled in Codex settings, it posts a review whenever a PR is opened and re-reviews on each pushed commit. Without it, an `@codex review` PR comment triggers a review. A plain `git push` to a non-PR branch never triggers it.
- **Thoroughness:** enable **Exhaustive code review** in Codex settings so Codex keeps looking for findings until it stops surfacing new ones — a policy reviewer runs to a clean bill rather than stopping at diminishing returns.
- **Authorship:** reviews are submitted by `chatgpt-codex-connector[bot]`; inline comments carry the same login.
- **Verdicts:** the app posts only `CHANGES_REQUESTED` (violations found) or `COMMENT` (clean / observations) — never `APPROVE`. A clean re-review after an earlier `CHANGES_REQUESTED` lands as a `COMMENT` that does not supersede the stale request in GitHub's merge gate, so Step 7 dismisses it via `skills/release/dismiss-stale-reviews.sh`.

## Copilot — second reviewer with a different lens

The skill keeps Copilot as a deliberate second reviewer alongside the Codex app, not as a temporary trial. They have complementary lenses: the Codex app enforces `rules/*.md` compliance (per `AGENTS.md ## Review guidelines`), while Copilot reads for correctness, bugs, security, and test-coverage gaps that no rule file specifically targets (scoped via `.github/copilot-instructions.md`). PRs through this skill regularly see each catch issues the other misses.

The operator requests Copilot via `skills/release/request-copilot-review.sh`. The script uses the GraphQL `requestReviews` mutation (REST drops bot reviewers silently — that's the failure mode the script exists to avoid), keeps a pinned bot ID `BOT_kgDOCnlnWA` for the hot path, and falls back to discovering the bot ID from recent reviews when the pin goes stale. It verifies Copilot landed in `requested_reviewers` before exiting. Exits non-zero on failure; emits a JSON summary on success. Both Copilot and the Codex reviewer gate the merge per Step 7.
