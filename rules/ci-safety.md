---
alwaysApply: true
---

# CI Safety

## Hands Off CI Config

- **Never modify CI configuration** (workflow files, pipeline configs) without explicitly asking first
- CI changes affect every contributor and every branch — they require careful review

## Never Skip Tests

- Never add `[skip ci]` to commit messages
- Never disable or skip failing tests to unblock a merge
- If tests fail, fix the tests or fix the code — skipping is not a valid option

## Install, Don't Skip

- If a test needs an external tool or dependency, install it in CI
- "It's hard to install" is not a reason to skip tests — figure out the installation

## Branch Naming

- Use the convention: `<type>/<description>` (e.g., `feat/add-auth`, `fix/null-pointer`, `chore/update-deps`)
- Keep branch names lowercase with hyphens

## Always Watch CI

- After every push, watch the CI run to completion — never assume it will pass
- Use `gh run watch` or equivalent to monitor the run in real time
- If CI fails, inspect the logs immediately, fix the issue, and push again
- A task is not done until CI is green

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
