# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Policy reviewer — two deployments

The policy reviewer reviews the PR diff against the in-tree `rules/*.md` and posts the verdict as a PR review. It runs one of two ways depending on the repo. Normally exactly one deployment reviews a given PR; a repo mid fleet-migration can transiently carry both, which is why the release scripts resolve the verdict fail-safe across legacy and App logins (`skills/release/poll-pr-reviews.sh` login table).

Both derive the review event from per-finding severity (`rules/review-severity.md`; `.github/codex-review/post-review.sh` header) — any blocking finding posts `REQUEST_CHANGES`; nonblocking results approve with any advisory notes included, falling back to `COMMENT` only when GitHub forbids approval.

When the posting identity already has a live verdict on the head, a later run posts nothing and exits 0. A dismissed review permits a fresh review on the same head. A failed review lookup fails the run without posting (`.github/codex-review/post-review.sh` header).

### A. coding-policy's own PRs — in-repo `review-codex.yml`

The `.github/workflows/review-codex.yml` GitHub Actions workflow runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key), via `codex exec` with `.github/codex-review/prompt.md` and `schema.json`.

- **Trigger:** fires on `pull_request` `opened` / `synchronize` / `reopened` — reviews on open and re-reviews each pushed commit. Fork PRs are skipped (no secret access) — adopt via `Skill(skill: "adopt-fork-pr")`.
- **Authorship:** submitted as `coding-policy-fleet-reviewer[bot]` using the same `FLEET_APP_ID` / `FLEET_APP_PRIVATE_KEY` as `fleet-review.yml`. The posting token selects this repository and requests Pull requests write. History reads use that App's login; posting and dedup use the minted App slug.
- **APPROVE:** a clean or advisory-only re-review approves and supersedes this App's earlier change request natively. Open PRs with legacy `github-actions[bot]` requests retain the migration cleanup in Step 7; an approval from another identity does not clear them automatically.
- **Recovery:** token creation fails if either existing App secret or this repository's installation grant is missing. Restore the existing App's installation with Pull requests write or re-seed its named secrets, then rerun the failed workflow. Verify the posted review's author, current `commit_id` and APPROVED state, not only the workflow conclusion. Never substitute the workflow token or Claude App. A new commit is required for re-review of a head that already has a live App verdict.
- **Auth / cost:** the subscription token is read only from `CODEX_AUTH_JSON` at runtime, never persisted to the runner (rules/no-secrets.md); Codex refreshes the access token in-memory. No per-token API billing. If the refresh token expires the review fails loudly and the operator re-seeds the secret. One-time setup — `codex login` plus `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json` — is in the `review-codex.yml` header.

### B. Consumer repos — central `coding-policy-fleet-reviewer[bot]` App

Consumer repos carry no per-repo Codex review workflow (`review-codex.yml`) and no `CODEX_AUTH_JSON` — only a thin `.github/workflows/review-trigger.yml`, one lightweight `FLEET_DISPATCH_TOKEN` PAT, and a `.github/fleet-review-enabled` marker that enrolls the repo in the scheduled backstop poll (scaffolded by `skills/onboard-repo/scaffold.sh`). The central fleet App (coding-policy#202) does the reviewing: the fleet-review run `tessl install`s `jbaruch/coding-policy` to a temp path and symlinks its `.tessl/` into the PR workspace, so the review runs against coding-policy's `rules/*.md` (see the policy install/symlink block in `.github/codex-review/fleet-review-one.sh` `main`), not a copy the consumer pre-installed.

- **Trigger:** the consumer's in-repo `.github/workflows/review-trigger.yml` fires on the same `pull_request` events and dispatches `fleet-review.yml` (single-PR `workflow_dispatch`) in coding-policy via the `FLEET_DISPATCH_TOKEN` PAT; a scheduled marker-gated cron poll in coding-policy is the backstop for any dispatch that never fired (cadence in the `fleet-review.yml` header).
- **Authorship:** submitted as `coding-policy-fleet-reviewer[bot]`.
- **APPROVE:** the App CAN `APPROVE`, so a clean re-review supersedes its own earlier `CHANGES_REQUESTED` natively — no dismissal step is needed on consumer repos. Which reviews are dismissal-eligible is the script's decision (see the `skills/release/dismiss-stale-reviews.sh` header).

## Claude — independent code-quality review

Codex checks policy compliance. Claude uses the replacement system prompt in
.github/claude-review/system-prompt.md for correctness, bugs, security,
reliability, performance and concrete test gaps. Its isolated session loads no
policy, agent instructions, memory, plugins, skills, hooks or MCP servers and
has only read-only source tools. Authentication uses CLAUDE_CODE_OAUTH_TOKEN
from claude setup-token; the token is held only in coding-policy. Its dedicated GitHub App uses CLAUDE_REVIEW_APP_ID and CLAUDE_REVIEW_APP_PRIVATE_KEY, also held only in coding-policy (see .env.example).

Both reviews run on PR events. The central fleet workflow runs them as separate
jobs using distinct reviewer Apps; existing consumer dispatches start both. The quality result posts as a
severity-derived verdict with the exact analyzed commit and the marker owned by
`skills/release/review-lanes.sh` — `CLAUDE_REVIEW_LOGIN` and `review_lanes_jq`. Snapshot and dedup use that classifier and the separate logins. Claude posts as jbaruch-claude-reviewer[bot], independently of the Codex policy identity. A live, correctly bound latest submission suppresses a duplicate. Invalid provenance allows a fresh review.

Claude approves nonblocking results, including advisory notes. An App's approval
supersedes its earlier change request; a plain comment would leave that request
active. The dedicated App can approve on coding-policy and consumer repositories.
The policy App follows the same native approval behavior in both deployments.

Both reviewers emit findings in their review bodies. Reply in a PR comment citing the review and finding; use an inline thread only for an actual inline comment. Claude blocking findings are fixed before merge.

Read both bodies before merge. On failed authentication, renew the subscription
token in coding-policy and rerun the failed job. Do not use API-key fallback or
request Copilot. The workflow pins the Claude CLI; recheck the model pin when
Renovate updates the CLI and at the weekly capability refresh.
