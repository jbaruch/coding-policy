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

- Tests must be deterministic — no self-generated random test data (narrow exception: Seeded Property-Based Test Carve-Out below)
- Provide fixed test data; never let runtime randomness generate or shape the test's inputs
- No dependence on the current date or wall-clock time — no `today`, runtime `now()`, or hardcoded future dates in assertions or fixtures (narrow exception: Live-Upstream Future-Date Carve-Out below)
- Control the clock: inject or freeze "now" (a passed-in reference date, a mocked time source, freezegun) so a test green today is green every day
- Compute relative dates from a fixed injected reference, never from the real clock at run time
- Fixed past dates as fixtures are fine — the ban is on time-relative values that rot as the run date advances
- Flaky tests are bugs — diagnose the root cause, don't retry and hope

## Live-Upstream Future-Date Carve-Out

- Narrow exception for suites exercising a live external service that rejects past-dated inputs (flight search, hotel booking, event scheduling)
- Versioned fixture files, prompt fixtures included, may pin explicit future dates
- Assertions compare against the pinned fixture dates — never against fresh future-date literals
- Preconditions (all required):
  1. Each pinned date is a literal in a versioned fixture — never computed from the run-time clock
  2. Fixtures carry the date in the filename (e.g., `fixture-2025-04-17.json`)
  3. The owning repo documents the refresh cadence and refresh procedure beside the fixtures
- A pinned date aging into the past is a fixture refresh under the documented cadence — never an inline patch during unrelated work
- Every other suite still bans future dates and time-relative values

## Seeded Property-Based Test Carve-Out

- Narrow exception for property-based tests whose generated inputs come from a pinned seed
- Applies when a generative testing library (kotest property tests, Hypothesis, fast-check, QuickCheck) explores many cases per run under a fixed seed
- Preconditions (all required):
  1. The generator seed is a constant in the test file (e.g. `PropTestConfig(seed = 1234)`), never drawn from the clock, the environment, or an unset default
  2. The iteration count is explicitly bounded (e.g. `iterations = 100`), never unbounded or environment-derived
  3. No runtime-RNG call (`random()`, unseeded `Random()`, `shuffle`, `now()`) selects or shapes the inputs
- Every other case still follows Determinism: unseeded or unbounded generation, and any runtime-RNG input, stays forbidden

## Fixtures

- No binary fixtures checked into the repo
- Build test data programmatically in test setup/fixtures (narrow exception: pinned future-date fixture files under the Live-Upstream Future-Date Carve-Out above)
- For binary files that can't be built programmatically, download them from a URL during test setup

## Independence

- Each test must run independently — no shared mutable state between tests
- Test order must not matter
- Clean up after yourself: temporary files, database state, mock patches
