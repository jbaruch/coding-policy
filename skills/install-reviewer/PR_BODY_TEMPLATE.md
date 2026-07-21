# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What this PR commits, and who reviews

Explain that this PR enrolls the repo in the central reviewer and nothing else:

- `.github/fleet-review-enabled` — the opt-in marker. Its presence enrolls this repo in the central `coding-policy-fleet-reviewer` GitHub App, a scheduled poller in `jbaruch/coding-policy` that reviews every open PR against the `jbaruch/coding-policy` rules with the OpenAI Codex CLI on a **ChatGPT subscription** (no API key). The verdict posts as a `<app-slug>[bot]` review.
- `.github/copilot-instructions.md` — scopes Copilot to the complementary lane (correctness, bugs, security, test gaps) and off policy.

The fleet reviewer is the policy reviewer; Copilot is the code-quality reviewer. Both gate the merge.

### 2. How the review runs — no repo secrets

State that the consumer sets **nothing**: the credential (`CODEX_AUTH_JSON`) and the `tessl install` token live only in `jbaruch/coding-policy`, where the poller runs. Once this PR merges to the default branch, the poller picks the repo up on its next cycle (fork PRs are skipped — they are adopted via the `adopt-fork-pr` skill).

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that the fleet review's summary begins `Policy loaded: N rule files from jbaruch/coding-policy.` and each finding names the violated rule in bold (e.g. `**ci-safety**`) — seeing the `Policy loaded:` line confirms the poller's `tessl install` loaded the policy. A summary that never reports `Policy loaded: N rule files` is a signal the install step did not run.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it later.

If Step 1 emitted no warnings, omit the section entirely.
