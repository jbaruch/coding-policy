# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What this PR commits, and who reviews

Explain that this PR adds a GitHub Actions reviewer and nothing else:

- `.github/workflows/review-codex.yml` — on every PR, installs the `jbaruch/coding-policy` rules via `tessl install` and reviews the diff against them with the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (no API key). The verdict posts as a `github-actions[bot]` review.
- `.github/codex-review/` — the review driver (`post-review.sh`) plus the credential guards (`mask-secrets.sh`, `assert-no-secret-leak.sh`) and the review `prompt.md` + `schema.json`.
- `.github/copilot-instructions.md` — scopes Copilot to the complementary lane (correctness, bugs, security, test gaps) and off policy.

The Codex reviewer is the policy reviewer; Copilot is the code-quality reviewer. Both gate the merge.

### 2. Operator secrets, before the reviewer runs

List the two repo secrets the consumer must set (Settings → Secrets and variables → Actions) BEFORE merging — the reviewer fails its first run without them:

- `CODEX_AUTH_JSON` — the ChatGPT-subscription token. On a trusted machine run `codex login` (Sign in with ChatGPT), confirm `~/.codex/auth.json` has `"auth_mode":"chatgpt"` and `"has_refresh_token":true`, then `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json`. Re-run that whenever the review fails on expired auth. See https://learn.chatgpt.com/docs/auth/ci-cd-auth
- `TESSL_TOKEN` — from https://tessl.io/account/api-keys, used by the workflow's `tessl install jbaruch/coding-policy` step.

Note the accepted security posture: this `pull_request` workflow exposes `CODEX_AUTH_JSON` to same-repo PRs (write-access collaborators, trusted per GitHub's model; forks are excluded). Leak vectors are mitigated — the token is `::add-mask::`-redacted from logs, the review output is scanned for it, and the token is deleted before the post step.

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that the Codex review cites findings as `coding-policy: <rule>` and its summary begins `Policy loaded: N rule files from jbaruch/coding-policy.` — seeing that confirms the workflow's `tessl install` loaded the policy. A review that never references a `coding-policy:` rule is a signal the install step did not run.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it later.

If Step 1 emitted no warnings, omit the section entirely.
