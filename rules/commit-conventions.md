---
alwaysApply: true
---

# Commit Conventions

## Commit Messages

- **Imperative mood** in the subject line: "Add feature", not "Added feature" or "Adds feature"
- Subject line ~50 characters; hard limit at 72
- Body explains **why**, not what
- Separate subject from body with a blank line

## One Change Per Commit

- Each commit represents one logical change
- Don't mix refactors with features, formatting with functional changes, or dependency updates with code changes
- If you need to refactor before implementing, that's a separate commit
- Reformatting goes in a dedicated commit before or after the functional change

## Pull Requests

- CI must pass before merging
- PR title follows `<type>(<scope>): <imperative summary>` format
- The PR title is the changelog line
- Keep PRs focused
