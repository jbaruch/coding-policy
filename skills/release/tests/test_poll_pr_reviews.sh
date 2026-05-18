#!/usr/bin/env bash
# Outcome-based tests for poll-pr-reviews.sh, focused on the new
# `merge_state` output path: `fetch_merge_state` must return the
# `{status, mergeable}` envelope keyed off GitHub's `mergeStateStatus`
# and `mergeable` fields, and `main` must surface that envelope as a
# top-level field in the snapshot JSON.
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that returns
# fixture JSON for the two surfaces poll-pr-reviews.sh calls —
# `gh pr checks` and `gh pr view --json mergeStateStatus,mergeable` —
# plus the two `gh api` surfaces for reviews/comments. jq runs locally
# so the script's filter logic is exercised, not duplicated in the test.
#
# Run: bash skills/release/tests/test_poll_pr_reviews.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/poll-pr-reviews.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: poll-pr-reviews.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

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

# Mock `gh` — supports the four invocations poll-pr-reviews.sh issues.
# MOCK_MERGE_STATE selects the fixture for `gh pr view`; other surfaces
# return minimal fixtures so main() can complete.
gh() {
  case "$1" in
    pr)
      case "$2" in
        view)
          # gh pr view <N> --repo <o/r> --json mergeStateStatus,mergeable
          case "${MOCK_MERGE_STATE:-}" in
            clean)        echo '{"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE"}' ;;
            dirty)        echo '{"mergeStateStatus":"DIRTY","mergeable":"CONFLICTING"}' ;;
            unknown)      echo '{"mergeStateStatus":"UNKNOWN","mergeable":"UNKNOWN"}' ;;
            *) echo "mock gh: unknown MOCK_MERGE_STATE='${MOCK_MERGE_STATE:-}'" >&2; return 2 ;;
          esac
          ;;
        checks)
          # gh pr checks <N> --repo <o/r> --json name,bucket
          echo '[]'
          ;;
        *) echo "mock gh pr: unsupported subcommand: $2" >&2; return 2 ;;
      esac
      ;;
    api)
      # gh api repos/<o>/<r>/pulls/<N>/reviews --jq <filter>
      # gh api repos/<o>/<r>/pulls/<N>/comments --jq <filter>
      local path="$2" filter=""
      shift 2
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --jq) filter="$2"; shift 2 ;;
          *)    shift ;;
        esac
      done
      local fixture='[]'
      if [[ -n "$filter" ]]; then
        echo "$fixture" | jq "$filter"
      else
        echo "$fixture"
      fi
      ;;
    *) echo "mock gh: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# --- test bodies ---

t_fetch_merge_state_clean_returns_mergeable_envelope() {
  MOCK_MERGE_STATE=clean
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "CLEAN"     "$status"    || return 1
  assert_eq "mergeable" "MERGEABLE" "$mergeable"
}

t_fetch_merge_state_dirty_returns_conflicting_envelope() {
  MOCK_MERGE_STATE=dirty
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "DIRTY"       "$status"    || return 1
  assert_eq "mergeable" "CONFLICTING" "$mergeable"
}

t_fetch_merge_state_unknown_returns_unknown_envelope() {
  MOCK_MERGE_STATE=unknown
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "UNKNOWN" "$status"    || return 1
  assert_eq "mergeable" "UNKNOWN" "$mergeable"
}

t_main_surfaces_merge_state_as_top_level_field() {
  MOCK_MERGE_STATE=clean
  local out keys
  out=$(main "owner" "repo" "1")
  keys=$(echo "$out" | jq -r '.merge_state | "\(.status)|\(.mergeable)"')
  assert_eq "merge_state in main output" "CLEAN|MERGEABLE" "$keys"
}

t_main_propagates_dirty_state() {
  MOCK_MERGE_STATE=dirty
  local out keys
  out=$(main "owner" "repo" "1")
  keys=$(echo "$out" | jq -r '.merge_state | "\(.status)|\(.mergeable)"')
  assert_eq "merge_state in main output" "DIRTY|CONFLICTING" "$keys"
}

# --- driver ---

echo "== poll-pr-reviews.sh tests =="
run "fetch_merge_state returns {CLEAN, MERGEABLE} for a clean PR"     t_fetch_merge_state_clean_returns_mergeable_envelope
run "fetch_merge_state returns {DIRTY, CONFLICTING} on conflict"      t_fetch_merge_state_dirty_returns_conflicting_envelope
run "fetch_merge_state propagates UNKNOWN/UNKNOWN while computing"    t_fetch_merge_state_unknown_returns_unknown_envelope
run "main surfaces merge_state as a top-level field"                  t_main_surfaces_merge_state_as_top_level_field
run "main propagates DIRTY merge_state end-to-end"                    t_main_propagates_dirty_state

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
