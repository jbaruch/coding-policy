#!/usr/bin/env bash
# Outcome-based tests for run-tests.sh — the CI test discoverer/runner.
# Asserts the exit-code contract callers (tests.yml, publish.yml) depend
# on: 0 when every discovered suite passes, 1 when any fails, 2 on a
# setup error (no suite found, or a missing base dir). Each case builds a
# throwaway fixture tree and points the runner at it via the base-dir arg
# so the runner never recurses into the real repo.
#
# Run: bash scripts/tests/test_run_tests.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

RUNNER="$(cd "$(dirname "$0")/.." && pwd)/run-tests.sh"
[[ -x "$RUNNER" ]] || { echo "fatal: run-tests.sh not executable at $RUNNER" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

# Build a fixture base dir under a fresh temp root. Each named suite is
# created as <base>/skills/<name>/tests/test_<name>.sh exiting with the
# given code.
make_base() {
  local base; base="$(mktemp -d)"
  echo "$base"
}
add_suite() {
  local base="$1" name="$2" exit_code="$3"
  local dir="$base/skills/$name/tests"
  mkdir -p "$dir"
  printf '#!/usr/bin/env bash\nexit %s\n' "$exit_code" > "$dir/test_$name.sh"
}

run_runner() {
  # echoes the exit code; captures nothing else
  "$RUNNER" "$1" >/dev/null 2>&1
  echo $?
}

echo "run-tests.sh tests"

# --- all suites pass -> exit 0 ---
base="$(make_base)"
add_suite "$base" alpha 0
add_suite "$base" beta 0
code="$(run_runner "$base")"
[[ "$code" == "0" ]] && pass "all suites pass -> exit 0" || fail "all pass: expected 0, got $code"
rm -rf "$base"

# --- one suite fails -> exit 1 ---
base="$(make_base)"
add_suite "$base" alpha 0
add_suite "$base" beta 1
code="$(run_runner "$base")"
[[ "$code" == "1" ]] && pass "one suite fails -> exit 1" || fail "one fail: expected 1, got $code"
rm -rf "$base"

# --- failing suite name is reported on stderr ---
base="$(make_base)"
add_suite "$base" alpha 0
add_suite "$base" doomed 1
err="$("$RUNNER" "$base" 2>&1 >/dev/null)"
echo "$err" | grep -q "test_doomed.sh" && pass "failing suite named on stderr" || fail "failing suite not named in stderr: $err"
rm -rf "$base"

# --- no suites found -> exit 2 ---
base="$(make_base)"  # empty, no suites
code="$(run_runner "$base")"
[[ "$code" == "2" ]] && pass "no suites found -> exit 2" || fail "empty: expected 2, got $code"
rm -rf "$base"

# --- missing base dir -> exit 2 ---
code="$(run_runner "/nonexistent/path/$$")"
[[ "$code" == "2" ]] && pass "missing base dir -> exit 2" || fail "missing base: expected 2, got $code"

# --- a later suite failing still fails the run (no early-exit masking) ---
base="$(make_base)"
add_suite "$base" aaa 0
add_suite "$base" zzz 1
code="$(run_runner "$base")"
[[ "$code" == "1" ]] && pass "later-suite failure not masked -> exit 1" || fail "later fail: expected 1, got $code"
rm -rf "$base"

echo ""
echo "run-tests.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
