---
alwaysApply: true
---

# Testing Standards

## Coverage

- Every module gets tests — no untested code ships
- Test file naming follows the project's convention (e.g., `test_*.py`, `*.test.ts`, `*_test.go`)

## Assertions

- Assert **outcomes**, not implementation details
- Test what the code does, not how it does it
- If an internal refactor breaks your tests, the tests were testing the wrong thing

## Determinism

- Tests must be deterministic — no self-generated random test data
- Provide fixed test data; never have the test generate its own inputs randomly
- Flaky tests are bugs — diagnose the root cause, don't retry and hope

## Fixtures

- No binary fixtures checked into the repo
- Build test data programmatically in test setup/fixtures
- For binary files that can't be built programmatically, download them from a URL during test setup

## Independence

- Each test must run independently — no shared mutable state between tests
- Test order must not matter
- Clean up after yourself: temporary files, database state, mock patches
