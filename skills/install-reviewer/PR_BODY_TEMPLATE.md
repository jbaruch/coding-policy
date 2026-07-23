# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What this PR commits, and who reviews

State up front that this repo and `jbaruch/coding-policy` share the **same owner**: `jbaruch/coding-policy` is the maintainer's own repository holding their coding rules and a shared review setup they run across their own repositories. This PR opts this repo into that same-owner review setup and commits nothing else:

- `.github/workflows/review-trigger.yml` — a thin `on: pull_request` workflow that asks the maintainer's own `jbaruch/coding-policy` to run a single-PR review of this PR, so the verdict lands before merge. It carries no review credential — only the narrow, least-privilege token below.
- `.github/fleet-review-enabled` — the opt-in marker. It enrolls this repo in the same-owner review setup's scheduled poll, the backstop for anything the trigger missed. Reviews run against the maintainer's published `jbaruch/coding-policy` rules with the OpenAI Codex CLI on a **ChatGPT subscription** (no API key); the verdict posts as a `<app-slug>[bot]` review.
- `.github/copilot-instructions.md` — scopes Copilot to the complementary lane (correctness, bugs, security, test gaps) and off policy.
- `.env.example` — a `FLEET_DISPATCH_TOKEN` entry carrying this repo's Actions-secrets settings URL (appended, never overwriting existing variables), documenting the required token per the `no-secrets` rule.

The maintainer's own policy reviewer is one lane; Copilot is the code-quality lane. Both gate the merge.

### 2. Operator secret — one least-privilege token

The review credential stays in the maintainer's own `jbaruch/coding-policy`; the consumer holds only a minimal token so the trigger can reach that same-owner repo:

- `FLEET_DISPATCH_TOKEN` — a **fine-grained token the maintainer scopes to only their own `jbaruch/coding-policy`** with `Actions: write` and nothing else (least privilege): it can ask that repo to run a review and do nothing more. Set it (Settings → Secrets and variables → Actions) before the first PR after merge, or the PR-time review won't fire (the poll backstop still would). Fork PRs are skipped — they can't read the secret; adopt them via the `adopt-fork-pr` skill. Dependabot PRs are skipped too — the dependabot actor gets no secrets, so the poll backstop reviews them instead.

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that the fleet review's summary begins `Policy loaded: N rule files from jbaruch/coding-policy.` and each finding names the violated rule in bold (e.g. `**ci-safety**`) — seeing the `Policy loaded:` line confirms the poller's `tessl install` loaded the policy. A summary that never reports `Policy loaded: N rule files` is a signal the install step did not run.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it later.

If Step 1 emitted no warnings, omit the section entirely.
