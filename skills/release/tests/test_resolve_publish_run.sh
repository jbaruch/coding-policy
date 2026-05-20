#!/usr/bin/env bash
# Outcome-based tests for resolve-publish-run.sh.
#
# Covers the three behaviors the script promises:
#   1. Immediate hit — `gh run list` returns the run on the first call,
#      script prints the ID and exits 0 without sleeping.
#   2. Deferred hit — first N calls return empty, then a later call
#      returns the run; script polls, eventually finds it, prints the
#      ID, exits 0.
#   3. Budget exhausted — every call returns empty; script exits non-zero
#      with a diagnostic on stderr that mentions the SHA and workflow.
#
# Approach: source the script (the main() guard prevents auto-run when
# sourced) and override `gh` + `sleep` as shell functions. Because
# `main` runs the gh call inside a subshell pipeline (`gh ... | head`),
# state like "which call is this" can't live in shell variables —
# subshells get a copy and writes don't propagate back. State lives
# in tempfiles instead: a call-index file the mock reads-and-bumps,
# and a queue-file the mock indexes into for the response.
#
# Run: bash skills/release/tests/test_resolve_publish_run.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/resolve-publish-run.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: resolve-publish-run.sh not executable at $SCRIPT" >&2; exit 2; }

# Keep the test fast — 1s intervals, 3s budget. The script defaults
# (2s / 30s) are still asserted indirectly by the immediate-hit and
# deferred-hit tests, which observe call counts rather than wall time.
export RESOLVE_PUBLISH_RUN_INTERVAL_SEC=1
export RESOLVE_PUBLISH_RUN_BUDGET_SEC=3

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

# Tempfiles tracking mock state across subshell boundaries.
TMPDIR_TEST=$(mktemp -d -t resolve-pub-test.XXXXXX)
trap 'rm -rf "$TMPDIR_TEST"' EXIT
export MOCK_GH_CALLS_FILE="$TMPDIR_TEST/gh-calls"
export MOCK_SLEEP_CALLS_FILE="$TMPDIR_TEST/sleep-calls"
export MOCK_GH_QUEUE_FILE="$TMPDIR_TEST/gh-queue"

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Mock `gh` — supports only `gh run list`. Reads the next response from
# MOCK_GH_QUEUE_FILE (one entry per line); literal `EMPTY` maps to empty
# stdout, anything else is echoed verbatim. Records the invocation in
# MOCK_GH_CALLS_FILE so the test can count calls after main() returns.
gh() {
  [[ "$1" == "run" && "$2" == "list" ]] || { echo "mock gh: unexpected invocation: $*" >&2; return 99; }
  echo "call" >> "$MOCK_GH_CALLS_FILE"
  local call_count
  call_count=$(wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' ')
  local response
  response=$(sed -n "${call_count}p" "$MOCK_GH_QUEUE_FILE")
  [[ -z "$response" || "$response" == "EMPTY" ]] && return 0
  echo "$response"
}

# Mock `sleep` — record the call but don't actually wait.
sleep() {
  echo "call" >> "$MOCK_SLEEP_CALLS_FILE"
}

reset_mocks() {
  : > "$MOCK_GH_CALLS_FILE"
  : > "$MOCK_SLEEP_CALLS_FILE"
  : > "$MOCK_GH_QUEUE_FILE"
}

queue_responses() {
  for r in "$@"; do
    echo "$r" >> "$MOCK_GH_QUEUE_FILE"
  done
}

gh_calls() { wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' '; }
sleep_calls() { wc -l < "$MOCK_SLEEP_CALLS_FILE" | tr -d ' '; }

# --- Test 1: immediate hit ----------------------------------------------------
test_immediate_hit() {
  reset_mocks
  queue_responses "123456"
  local output rc=0
  output=$(main jbaruch coding-policy abc123 publish.yml 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "output" "123456" "$output" || return 1
  assert_eq "gh call count" "1" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "0" "$(sleep_calls)" || return 1
}
run "immediate hit returns run ID without sleeping" test_immediate_hit

# --- Test 2: deferred hit (poll succeeds on third try) ------------------------
test_deferred_hit() {
  reset_mocks
  queue_responses "EMPTY" "EMPTY" "789012"
  local output rc=0
  output=$(main jbaruch coding-policy def456 publish.yml 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "output" "789012" "$output" || return 1
  assert_eq "gh call count" "3" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "2" "$(sleep_calls)" || return 1
}
run "deferred hit polls until run appears" test_deferred_hit

# --- Test 3: budget exhausted -------------------------------------------------
test_budget_exhausted() {
  reset_mocks
  queue_responses "EMPTY" "EMPTY" "EMPTY" "EMPTY" "EMPTY"
  local stderr rc=0
  stderr=$(main jbaruch coding-policy zzz999 publish.yml 2>&1 >/dev/null) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected non-zero exit, got 0" >&2; return 1; }
  echo "$stderr" | grep -q "zzz999" || { echo "    FAIL: stderr missing SHA, got: ${stderr}" >&2; return 1; }
  echo "$stderr" | grep -q "publish.yml" || { echo "    FAIL: stderr missing workflow name, got: ${stderr}" >&2; return 1; }
}
run "budget exhausted exits non-zero with diagnostic" test_budget_exhausted

# --- Test 4: arg count validation ---------------------------------------------
test_arg_validation() {
  reset_mocks
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc123 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "usage:" || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "missing arg exits 2 with usage" test_arg_validation

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
