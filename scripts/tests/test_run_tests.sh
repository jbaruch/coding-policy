#!/usr/bin/env bash
# Outcome-based tests for run-tests.sh — the CI test discoverer/runner.
# Asserts the contract callers depend on:
#   - exit code: 0 all-pass, 1 any-fail, 2 setup error (no suite / bad base)
#   - stdout: a single valid JSON summary
#       {"suites":N,"passed":P,"failed":F,"failures":[...]}
#   - human progress goes to stderr, never stdout (so stdout stays JSON)
# Each case builds a throwaway fixture tree and points the runner at it via
# the base-dir arg so the runner never recurses into the real repo.
#
# Run: bash scripts/tests/test_run_tests.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

RUNNER="$(cd "$(dirname "$0")/.." && pwd)/run-tests.sh"
[[ -x "$RUNNER" ]] || { echo "fatal: run-tests.sh not executable at $RUNNER" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq required for these tests" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

make_base() { mktemp -d; }
add_suite() {
  local base="$1" name="$2" exit_code="$3"
  local dir="$base/skills/$name/tests"
  mkdir -p "$dir"
  printf '#!/usr/bin/env bash\nexit %s\n' "$exit_code" > "$dir/test_$name.sh"
}

# Runs the runner; sets OUT (stdout), ERR (stderr), CODE (exit).
invoke() {
  local base="$1" errf; errf="$(mktemp)"
  OUT="$("$RUNNER" "$base" 2>"$errf")"; CODE=$?
  ERR="$(cat "$errf")"; rm -f "$errf"
}

echo "run-tests.sh tests"

# --- all suites pass -> exit 0, JSON summary ---
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" beta 0
invoke "$base"
{ [[ "$CODE" == 0 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 0 ]] \
  && [[ "$(jq -r '.failures | length' <<<"$OUT")" == 0 ]]; } \
  && pass "all pass -> exit 0, passed=2 failed=0" \
  || fail "all pass: code=$CODE out=$OUT"
rm -rf "$base"

# --- one suite fails -> exit 1, JSON names it in failures ---
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" doomed 1
invoke "$base"
{ [[ "$CODE" == 1 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 1 ]] \
  && jq -e '.failures | any(test("test_doomed.sh$"))' <<<"$OUT" >/dev/null; } \
  && pass "one fail -> exit 1, failures lists the suite" \
  || fail "one fail: code=$CODE out=$OUT"
rm -rf "$base"

# --- stdout is pure JSON; progress lives on stderr ---
base="$(make_base)"; add_suite "$base" alpha 0
invoke "$base"
{ jq -e . <<<"$OUT" >/dev/null \
  && ! grep -q "▶" <<<"$OUT" \
  && grep -q "▶" <<<"$ERR"; } \
  && pass "stdout is JSON, progress on stderr" \
  || fail "stream split: out=$OUT err=$ERR"
rm -rf "$base"

# --- no suites found -> exit 2, JSON error, suites=0 ---
base="$(make_base)"
invoke "$base"
{ [[ "$CODE" == 2 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 0 ]] \
  && jq -e 'has("error")' <<<"$OUT" >/dev/null; } \
  && pass "no suites -> exit 2, JSON error" \
  || fail "no suites: code=$CODE out=$OUT"
rm -rf "$base"

# --- missing base dir -> exit 2, JSON error ---
invoke "/nonexistent/path/$$"
{ [[ "$CODE" == 2 ]] && jq -e 'has("error")' <<<"$OUT" >/dev/null; } \
  && pass "missing base dir -> exit 2, JSON error" \
  || fail "missing base: code=$CODE out=$OUT"

# --- a later suite failing still fails the run (no early-exit masking) ---
base="$(make_base)"; add_suite "$base" aaa 0; add_suite "$base" zzz 1
invoke "$base"
{ [[ "$CODE" == 1 ]] && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]]; } \
  && pass "later-suite failure not masked -> exit 1" \
  || fail "later fail: code=$CODE out=$OUT"
rm -rf "$base"

echo ""
echo "run-tests.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
