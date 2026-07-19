# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Codex policy reviewer (Codex CLI on a ChatGPT subscription)

The policy reviewer is the `.github/workflows/review-codex.yml` GitHub Actions workflow. It runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key), reviewing the PR diff against the in-tree `rules/*.md` via `codex exec` with `.github/codex-review/prompt.md` and `schema.json`; `.github/codex-review/post-review.sh` submits the verdict as a PR review.

- **Trigger:** the workflow fires on `pull_request` `opened` / `synchronize` / `reopened`, so it reviews when the PR opens and re-reviews each pushed commit (Step 6 re-polls after every push). Fork PRs are skipped (no secret access) — adopt them via `adopt-fork-pr`.
- **Authorship:** the review is submitted with the workflow's `GITHUB_TOKEN`, so its author is `github-actions[bot]`.
- **Verdicts:** `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so a clean pass is a `COMMENT` and a violation is `REQUEST_CHANGES`. A clean re-review after an earlier `CHANGES_REQUESTED` lands as a `COMMENT` that does not supersede the stale request in GitHub's merge gate, so Step 7 dismisses it via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the subscription token is read only from the `CODEX_AUTH_JSON` secret at runtime and never persisted to the runner (rules/no-secrets.md); Codex refreshes the access token in-memory for the run. No per-token API billing. If the refresh token expires, the review fails loudly and the operator re-seeds the secret. One-time operator setup — `codex login` plus `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json` — is documented in the `review-codex.yml` header.

## Copilot — second reviewer with a different lens

The skill keeps Copilot as a deliberate second reviewer alongside the Codex reviewer, not as a temporary trial. They have complementary lenses: the Codex reviewer enforces `rules/*.md` compliance (per `.github/codex-review/prompt.md` and `AGENTS.md ## Review guidelines`), while Copilot reads for correctness, bugs, security, and test-coverage gaps that no rule file specifically targets (scoped via `.github/copilot-instructions.md`). PRs through this skill regularly see each catch issues the other misses.

The operator requests Copilot via `skills/release/request-copilot-review.sh`. The script uses the GraphQL `requestReviews` mutation (REST drops bot reviewers silently — that's the failure mode the script exists to avoid), keeps a pinned bot ID `BOT_kgDOCnlnWA` for the hot path, and falls back to discovering the bot ID from recent reviews when the pin goes stale. It verifies Copilot landed in `requested_reviewers` before exiting. Exits non-zero on failure; emits a JSON summary on success. Both Copilot and the Codex reviewer gate the merge per Step 7.
