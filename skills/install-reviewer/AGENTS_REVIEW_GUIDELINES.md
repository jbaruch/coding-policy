<!-- BEGIN jbaruch/coding-policy review guidelines -->
## Review guidelines

This repository installs the `jbaruch/coding-policy` rules as its PR review policy, enforced by the
OpenAI Codex code-review app. The Codex environment setup runs `tessl install jbaruch/coding-policy`,
materializing the rules under `.tessl/plugins/jbaruch/coding-policy/rules/`. When reviewing a pull request:

- Read every rule under `.tessl/plugins/jbaruch/coding-policy/rules/*.md` and review the diff against it.
- Also read any `skills/*/SKILL.md` that governs a changed path and check it against `skill-authoring`.
- Cite each finding as `coding-policy: <rule>` (e.g. `coding-policy: ci-safety`) with the file, the line,
  the clause violated, and the fix.
- General code quality — correctness, bugs, security, test-coverage gaps — is Copilot's lane
  (`.github/copilot-instructions.md`). Focus these guidelines on policy compliance.
<!-- END jbaruch/coding-policy review guidelines -->
