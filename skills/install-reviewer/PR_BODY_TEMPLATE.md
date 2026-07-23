# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What this PR adds

List the files this PR adds to run the `jbaruch/coding-policy` coding-rule review on this repo's pull requests:

- `.github/workflows/review-trigger.yml` — on each pull request, starts a review of that PR against the `jbaruch/coding-policy` rules so the result is available before merge.
- `.github/fleet-review-enabled` — an opt-in marker; while it is present, the same review also runs on a schedule as a backstop. The result posts as a `<app-slug>[bot]` review.
- `.github/copilot-instructions.md` — points Copilot at the code-quality lane (correctness, bugs, security, test gaps).
- `.env.example` — records the `FLEET_DISPATCH_TOKEN` name and where to set it, per the `no-secrets` rule.

The rule review and Copilot are separate lanes. The rule review gates the merge on blocking findings (correctness, security, policy-contract); Copilot is always advisory — read it, but it never gates.

### 2. Required secret

- `FLEET_DISPATCH_TOKEN` — the token `.github/workflows/review-trigger.yml` reads. Set it at Settings → Secrets and variables → Actions before the first PR after merge; until then only the scheduled backstop runs. Fork and Dependabot pull requests skip the trigger (they can't read the secret) and the scheduled backstop covers them.

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that the fleet review's summary begins `Policy loaded: N rule files from jbaruch/coding-policy.` and each finding names the violated rule in bold (e.g. `**ci-safety**`) — seeing the `Policy loaded:` line confirms the poller's `tessl install` loaded the policy. A summary that never reports `Policy loaded: N rule files` is a signal the install step did not run.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it later.

If Step 1 emitted no warnings, omit the section entirely.
