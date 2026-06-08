---
alwaysApply: true
---

# Testing Standards

## Coverage

- Every module gets tests — no untested code ships (narrow exception: Platform-Bound Untestable Carve-Out below)
- Test file naming follows the project's convention (e.g., `test_*.py`, `*.test.ts`, `*_test.go`)

## Platform-Bound Untestable Carve-Out

- Narrow exception for code that cannot execute on the project's CI runners
- Applies when the code drives an external desktop app, OS-level automation, or a proprietary runtime the runners cannot host (VBA macros driven via AppleScript against Microsoft PowerPoint, GUI automation gated on OS consent)
- Preconditions (all required):
  1. The deterministic, CI-runnable pieces are extracted and unit-tested — parsing, normalization, and data-shaping helpers split out of the automation wrapper. Only the genuinely-unhostable layer is exempt
  2. A manual validation procedure for the exempt layer is documented — what to run, what to observe, what counts as a pass
  3. The consuming plugin's authority-of-record rule names this carve-out, each exempt artifact, and where its validation procedure lives
- "Hard to install in CI" does NOT qualify — install the tool and test it per `rules/ci-safety.md` Install, Don't Skip
- Every other module still ships tests that run in CI

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
