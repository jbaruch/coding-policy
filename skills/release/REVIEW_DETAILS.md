# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired. Pulled out of SKILL.md so the main flow stays focused on what the operator does.

## Policy reviewer — two deployments

The policy reviewer reviews the PR diff against the in-tree `rules/*.md` and posts the verdict as a PR review. It runs one of two ways depending on the repo. Normally exactly one deployment reviews a given PR; a repo mid fleet-migration can transiently carry both, which is why the release scripts resolve the verdict fail-safe across both logins (`skills/release/poll-pr-reviews.sh` login table).

Both derive the review event from per-finding severity (`rules/review-severity.md`; `.github/codex-review/post-review.sh` header) — any blocking finding posts `REQUEST_CHANGES`, advisory-only findings post `COMMENT`, a clean pass posts `APPROVE`.

### A. coding-policy's own PRs — in-repo `review-codex.yml`

The `.github/workflows/review-codex.yml` GitHub Actions workflow runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key), via `codex exec` with `.github/codex-review/prompt.md` and `schema.json`.

- **Trigger:** fires on `pull_request` `opened` / `synchronize` / `reopened` — reviews on open and re-reviews each pushed commit. Fork PRs are skipped (no secret access) — adopt via `adopt-fork-pr`.
- **Authorship:** submitted with the workflow's `GITHUB_TOKEN`, so the author is `github-actions[bot]`.
- **APPROVE limitation:** `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so a clean pass falls back to `COMMENT`. A clean or advisory-only re-review after an earlier `CHANGES_REQUESTED` lands as a `COMMENT` that does NOT supersede the stale request in GitHub's merge gate, so Step 7 dismisses it via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the subscription token is read only from `CODEX_AUTH_JSON` at runtime, never persisted to the runner (rules/no-secrets.md); Codex refreshes the access token in-memory. No per-token API billing. If the refresh token expires the review fails loudly and the operator re-seeds the secret. One-time setup — `codex login` plus `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json` — is in the `review-codex.yml` header.

### B. Consumer repos — central `coding-policy-fleet-reviewer[bot]` App

Consumer repos carry no per-repo Codex review workflow (`review-codex.yml`) and no `CODEX_AUTH_JSON` — only a thin `.github/workflows/review-trigger.yml`, one lightweight `FLEET_DISPATCH_TOKEN` PAT, and a `.github/fleet-review-enabled` marker that enrolls the repo in the scheduled backstop poll (scaffolded by `onboard-repo`). The central fleet App (coding-policy#202) does the reviewing: the fleet-review run `tessl install`s `jbaruch/coding-policy` to a temp path and symlinks its `.tessl/` into the PR workspace, so the review runs against coding-policy's `rules/*.md` (see the policy install/symlink block in `.github/codex-review/fleet-review-one.sh` `main`), not a copy the consumer pre-installed.

- **Trigger:** the consumer's in-repo `.github/workflows/review-trigger.yml` fires on the same `pull_request` events and dispatches `fleet-review.yml` (single-PR `workflow_dispatch`) in coding-policy via the `FLEET_DISPATCH_TOKEN` PAT; a scheduled marker-gated cron poll in coding-policy is the backstop for any dispatch that never fired (cadence in the `fleet-review.yml` header).
- **Authorship:** submitted as `coding-policy-fleet-reviewer[bot]`.
- **APPROVE:** the App CAN `APPROVE`, so a clean re-review supersedes its own earlier `CHANGES_REQUESTED` natively — no dismissal step is needed on consumer repos. Which reviews are dismissal-eligible is the script's decision (see the `skills/release/dismiss-stale-reviews.sh` header).

## Copilot — second reviewer with a different lens

The skill keeps Copilot as a deliberate second reviewer alongside the policy reviewer, not as a temporary trial. They have complementary lenses: the policy reviewer enforces `rules/*.md` compliance (per `.github/codex-review/prompt.md` and `AGENTS.md ## Review guidelines`), while Copilot reads for correctness, bugs, security, and test-coverage gaps that no rule file specifically targets (scoped via `.github/copilot-instructions.md`). PRs through this skill regularly see each catch issues the other misses.

The operator requests Copilot via `skills/release/request-copilot-review.sh <owner> <repo> <pr>` — it requests the Copilot reviewer and verifies it landed, exiting non-zero on failure and emitting a JSON summary on success. The request mechanism, bot-id handling, and fallback discovery live in the script header (`rules/script-as-black-box.md`). Copilot is always advisory (`rules/review-severity.md`): its comments must be read (`rules/reviewer-feedback-reading.md`) but never gate the merge — only the policy reviewer's blocking findings gate per Step 7.

## Weighed findings and the ruled dismissal

Reference for Step 6 — how a marginal blocking finding reaches a weighing and how the operator's ruling clears the gating policy review (`rules/review-severity.md` Judge-Weighed Finding Carve-Out; `rules/ci-safety.md` Judge-Ruled-Review Dismissal Carve-Out). The operator is the judge; the agent nominates.

### The question

Nominate only a finding on lines the previous fix push added, or one you can support with a cited reachability claim — a path from input to the flagged line, with file and line citations. The claim travels in the question itself; no separate report is needed. Ask one question per gate, carrying every nomination:

```text
Weighing request at <head sha>. For each finding answer fix, defer or decline:
1. <source> <path>:<line> <rule> — <finding text> — nominated because <added by the last fix push | reachability claim with citations>
Floors are always fix. I keep fixing meanwhile; no answer means fix.
```

The question never blocks. Keep working the fix loop; a finding the operator has not answered stays blocking.

### The ruling file

Write the operator's answer into a ruling file. Its format, `schema_version`, required `ANSWER:` line and writer/reader contract are in the `skills/release/dismiss-ruled-review.sh` header. Keep the file for the life of the PR; the dismissal message pins its sha256 digest.

### Order of work

1. Run `dismiss-ruled-review.sh` with `--ruling` and `--followup-issue` (Step 6 command block). It posts the ruled findings to the follow-up issue — `defer`, or `decline` labelled won't-fix — then dismisses the review. Exit 1 names each unmet condition in `.unmet`; fix those findings instead.
2. Reply citing the ruling, with `<digest>` the 16-hex digest from the script's `.message`. Policy findings live in the review body, so their replies go in one PR comment; Copilot findings get a reply on their own thread:
   - Declined: `Declining — judge ruling <digest>: <reason from the ruling>`
   - Deferred: `Acknowledged — deferred to #<follow-up issue> (judge ruling <digest>)`
3. Run `dismiss-stale-reviews.sh` (Step 7), then re-run the Step 5 watcher. `poll-pr-reviews.sh` reads the dismissed review as `RULED`, which the watcher's ready test accepts.

A later push re-runs the policy reviewer. Run the script again with the same ruling: it dismisses the new review when the ruling still covers it, and exits 1 naming each finding it no longer covers otherwise. Those go back to a new question or the fix loop.
