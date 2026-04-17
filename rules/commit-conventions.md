---
alwaysApply: true
---

# Commit Conventions

## Commit Messages

- **Imperative mood** in the subject line: "Add feature", not "Added feature" or "Adds feature"
- Subject line ~50 characters; hard limit at 72
- Body explains **why**, not what — the diff shows what changed
- Separate subject from body with a blank line

## One Change Per Commit

- Each commit represents one logical change
- Don't mix refactors with features, formatting with bug fixes, or dependency updates with code changes
- If you need to refactor before implementing, that's a separate commit

## Pull Requests

- CI must pass before merging — no exceptions
- PR title follows `<type>(<scope>): <imperative summary>` format
- Add a changelog entry for user-visible changes
- Keep PRs focused — large PRs are hard to review and risky to merge
