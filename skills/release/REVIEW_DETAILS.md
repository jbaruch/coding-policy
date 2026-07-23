# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Codex policy reviewer (Codex CLI on a ChatGPT subscription)

The policy reviewer is the `.github/workflows/review-codex.yml` GitHub Actions workflow. It runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key), reviewing the PR diff against the in-tree `rules/*.md` via `codex exec` with `.github/codex-review/prompt.md` and `schema.json`; `.github/codex-review/post-review.sh` submits the verdict as a PR review.

- **Trigger:** the workflow fires on `pull_request` `opened` / `synchronize` / `reopened`, so it reviews when the PR opens and re-reviews each pushed commit (Step 6 re-polls after every push). Fork PRs are skipped (no secret access) — adopt them via `adopt-fork-pr`.
- **Authorship:** the review is submitted with the workflow's `GITHUB_TOKEN`, so its author is `github-actions[bot]`.
- **Verdicts:** the event is derived from per-finding severity (`rules/review-severity.md`; `post-review.sh` header) — a blocking finding posts `REQUEST_CHANGES`, advisory-only findings post `COMMENT`, and a clean pass posts `APPROVE`. `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so its clean pass falls back to `COMMENT`. A clean or advisory-only re-review after an earlier `CHANGES_REQUESTED` lands as a `COMMENT` that does not supersede the stale request in GitHub's merge gate, so Step 7 dismisses it via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the subscription token is read only from the `CODEX_AUTH_JSON` secret at runtime and never persisted to the runner (rules/no-secrets.md); Codex refreshes the access token in-memory for the run. No per-token API billing. If the refresh token expires, the review fails loudly and the operator re-seeds the secret. One-time operator setup — `codex login` plus `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json` — is documented in the `review-codex.yml` header.

## Copilot — second reviewer with a different lens

The skill keeps Copilot as a deliberate second reviewer alongside the Codex reviewer, not as a temporary trial. They have complementary lenses: the Codex reviewer enforces `rules/*.md` compliance (per `.github/codex-review/prompt.md` and `AGENTS.md ## Review guidelines`), while Copilot reads for correctness, bugs, security, and test-coverage gaps that no rule file specifically targets (scoped via `.github/copilot-instructions.md`). PRs through this skill regularly see each catch issues the other misses.

The operator requests Copilot via `skills/release/request-copilot-review.sh <owner> <repo> <pr>` — it requests the Copilot reviewer and verifies it landed, exiting non-zero on failure and emitting a JSON summary on success. The request mechanism, bot-id handling, and fallback discovery live in the script header (`rules/script-as-black-box.md`). Copilot is always advisory (`rules/review-severity.md`): its comments must be read (`rules/reviewer-feedback-reading.md`) but never gate the merge — only the policy reviewer's blocking findings gate per Step 7.
