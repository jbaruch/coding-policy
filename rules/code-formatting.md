---
alwaysApply: true
---

# Code Formatting

## Use the Project's Tools

- Use whatever formatter and linter the project already has configured
- Don't introduce a new formatter without team consensus
- Let the tooling decide style

## CI Integration

- Run format/lint checks before tests in CI
- Format checks should be a separate CI step that runs fast

## Separation of Concerns

- Commit formatting changes per `rules/commit-conventions.md` One Change Per Commit

## Basics

- Remove trailing whitespace
- End files with a single newline
- Indent consistently to the project standard
- Enforce these through editor config or pre-commit hooks, not manual effort
