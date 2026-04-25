# Install-Reviewer PR Body Template

Reference for Step 7 of the `install-reviewer` skill. Pulled out of SKILL.md so the main flow stays scannable.

## Required content

The PR body the skill opens must include the following content blocks. Order them as below.

### 1. What the workflows do, and the cross-family rule

Explain that the two workflows install `jbaruch/coding-policy` at run time and review every PR against it. The OpenAI and Anthropic reviewers each self-gate on the PR's `Author-Model:` declaration so the active reviewer is cross-family whenever the declaration permits. When the declaration spans both paired families, or neither paired family, both reviewers run as the documented fallback. See `jbaruch/coding-policy: author-model-declaration` on the registry.

### 2. Required secrets, before merge

List the three repository secrets the consumer must set BEFORE merging:

- `OPENAI_API_KEY` — OpenAI billing account for the Codex reviewer
- `ANTHROPIC_API_KEY` — Anthropic billing account for the Claude Code reviewer
- `TESSL_TOKEN` — generated at https://tessl.io/account/api-keys, used by the workflow's `tessl install` setup step

Add a one-line note that merging without all three secrets set will cause the workflows to fail on their first run.

### 3. Load indicator (so the consumer can confirm policy actually loaded)

Note that each reviewer's verdict begins with a one-line load indicator:

```
Policy loaded: N rule files from /tmp/gh-aw/coding-policy/.tessl/tiles/jbaruch/coding-policy/rules/ (installed tile).
```

Tells the consumer at a glance whether the workflow setup `tessl install` ran cleanly or whether the policy never reached the runtime.

### 4. (Conditional) "Action required before merge" — only if Step 1 preflight emitted warnings

If Step 1's preflight `warnings` array is non-empty, add an `## Action required before merge` section that reproduces each warning's `reason` verbatim. These are advisory findings the install-reviewer skill deliberately does NOT auto-fix — the section exists so the consumer sees and acts on the finding instead of discovering it via a failed workflow.

If Step 1 emitted no warnings, omit the section entirely.
