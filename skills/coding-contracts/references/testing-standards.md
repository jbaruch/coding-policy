# Testing Standards

## Scope

- Governs code the project ships: the published artifact, everything CI builds from it, and the tests covering both
- Narrow exception for an instrument tree.
- Instrument tree: code whose OUTPUT is the deliverable, read by a human (live-upstream probes, reverse-engineering scratch, one-off measurement scripts)
- An instrument tree falls outside this rule in full — every section below, not Coverage alone
- Preconditions (all required):
  1. Packaging configuration excludes the tree from the shipped artifact
  2. No shipped module imports from the tree
- A module the published artifact reaches does NOT qualify, whatever directory holds it
- "It is scratch" does NOT qualify on its own — both preconditions are required
- Every other tree in the repo follows this rule in full

## Coverage

- Every module gets tests — no untested code ships (narrow exception: Platform-Bound Untestable Carve-Out below)
- Test file naming follows the project's convention (e.g., `test_*.py`, `*.test.ts`, `*_test.go`)

## Platform-Bound Untestable Carve-Out

- Narrow exception for code that cannot execute on the project's CI runners
- Applies when the code drives an external desktop app, OS-level automation, or a proprietary runtime the runners cannot host (VBA macros driven via AppleScript against Microsoft PowerPoint, GUI automation gated on OS consent)
- Preconditions (all required):
  1. The deterministic, CI-runnable pieces are unit-tested. Only the genuinely-unhostable layer is exempt
  2. A manual validation procedure for the exempt layer is documented — what to run, what to observe, what counts as a pass
  3. Existing project documentation identifies the exempt interaction layer and its validation procedure
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
- A shipped bounded classifier is stubbed or replayed from recorded fixtures, never called live in a test
- Flaky tests are bugs — diagnose the root cause, don't retry and hope

## Live-Upstream Future-Date Carve-Out

- Narrow exception for suites exercising a live external service that rejects past-dated inputs (flight search, hotel booking, event scheduling)
- Versioned fixture files, prompt fixtures included, may pin explicit future dates
- Assertions compare against the pinned fixture dates — never against fresh future-date literals
- Preconditions (all required):
  1. Each pinned date is a literal in a versioned fixture — never computed from the run-time clock
  2. The owning repo documents the refresh cadence and refresh procedure
- A pinned date aging into the past is a fixture refresh under the documented cadence — never an inline patch during unrelated work
- Every other suite still bans future dates and time-relative values

## Seeded Property-Based Test Carve-Out

- Narrow exception for property-based tests whose generated inputs come from a pinned seed
- Applies when a generative testing library (kotest property tests, Hypothesis, fast-check, QuickCheck) explores many cases per run under a fixed seed
- Preconditions (all required):
  1. The generator uses a fixed reproducible seed
  2. Generation is bounded and reproducible
  3. No uncontrolled randomness selects or shapes inputs
- Every other case still follows Determinism: unseeded or unbounded generation and uncontrolled randomness selecting or shaping inputs stay forbidden

## Fixtures

- No binary fixtures checked into the repo
- Build test data programmatically in test setup/fixtures (narrow exception: pinned future-date fixture files under the Live-Upstream Future-Date Carve-Out above)
- For binary files that can't be built programmatically, download them from a URL during test setup

## Independence

- Each test must run independently — no shared mutable state between tests
- Test order must not matter
- Clean up after yourself: temporary files, database state, mock patches
