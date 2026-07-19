# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What this PR commits, and who reviews

Explain that this PR commits two repo artifacts and nothing else:

- `AGENTS.md` — a `## Review guidelines` section steering the OpenAI Codex code-review app to review every PR against the `jbaruch/coding-policy` rules (loaded by the Codex environment's `tessl install jbaruch/coding-policy` step).
- `.github/copilot-instructions.md` — scopes Copilot to the complementary lane (correctness, bugs, security, test gaps) and off policy, so the two reviewers do not overlap.

The Codex app is the policy reviewer; Copilot is the code-quality reviewer. Both gate the merge.

### 2. Operator setup, before the reviewer runs

The reviewer is a GitHub App on a ChatGPT subscription — it is enabled in the Codex UI, not by committing this PR. List the operator steps the consumer must complete (these cannot be automated):

1. Install the **OpenAI Codex** GitHub App on this repository and authenticate it to a ChatGPT plan that includes Codex code review.
2. In **Codex settings**, turn on **Code review** for this repository and turn on **Automatic reviews** (otherwise every PR needs a manual `@codex review` comment).
3. Configure the Codex **environment** for this repository to run `tessl install jbaruch/coding-policy` in its setup, and set any secret that step needs (e.g. a Tessl API key if your registry requires auth).

Add a one-line note that until steps 1–2 are done, the Codex reviewer will not post and the merge gate will wait on a reviewer that never runs.

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that the Codex review cites findings as `coding-policy: <rule>` — seeing a citation to an installed rule (not just a universal code smell) confirms the environment's `tessl install` loaded the policy. A review that never references a `coding-policy:` rule is a signal the environment setup did not run.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it later.

If Step 1 emitted no warnings, omit the section entirely.
