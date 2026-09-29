#!/usr/bin/env bash
# Outcome tests for copilot-run.sh's copilot_run_in_flight predicate (#641):
# a run is in flight when a Copilot request exists and the last
# `copilot_work_started` comes after both the last Copilot request and the last
# Copilot review on the timeline.
# The two callers (poll-pr-reviews.sh, request-copilot-review.sh) cover the
# end-to-end paths; this suite covers the predicate's edges.
#
# Approach: source the library and override `gh` with a shell function that
# prints TIMELINE_FIXTURE for the paginated timeline read.
#
# Run: bash skills/release/tests/test_copilot_run.sh

set -uo pipefail

LIB="$(cd "$(dirname "$0")/.." && pwd)/copilot-run.sh"
[[ -f "$LIB" ]] || { echo "fatal: copilot-run.sh not found at $LIB" >&2; exit 2; }

# shellcheck source=skills/release/copilot-run.sh
source "$LIB" || { echo "fatal: sourcing $LIB failed" >&2; exit 2; }
# The library turns on `set -e`; this harness aggregates results without it.
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
    echo "  pass: $name" >&2
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# shellcheck disable=SC2329  # invoked indirectly through the sourced predicate
gh() {
  if [[ "$1" == api && "$2" == --paginate && "$3" == repos/*/issues/*/timeline* ]]; then
    if [[ -n "${TIMELINE_FAIL:-}" ]]; then
      echo "gh: HTTP 502" >&2
      return 1
    fi
    printf '%s' "${TIMELINE_FIXTURE:-[]}"
    return 0
  fi
  echo "mock gh: unsupported: $*" >&2
  return 2
}

REQ='{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T18:31:54Z"}'
START='{"event":"copilot_work_started","created_at":"2026-09-29T18:32:51Z"}'
REVIEW='{"event":"reviewed","user":{"login":"Copilot"},"submitted_at":"2026-09-29T18:40:21Z"}'
HUMAN_REQ='{"event":"review_requested","requested_reviewer":{"login":"alice"},"created_at":"2026-09-29T18:35:00Z"}'
LATE_REQ='{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T18:36:33Z"}'

in_flight_for() { # <timeline-json>
  TIMELINE_FIXTURE="$1" copilot_run_in_flight owner repo 1
}

t_empty_timeline_is_not_in_flight() {
  assert_eq "in_flight" "false" "$(in_flight_for '[]')"
}

# #641: a run start with no Copilot request on the timeline is not a run the
# request/remove logic owns.
t_start_without_request_is_not_in_flight() {
  assert_eq "in_flight" "false" "$(in_flight_for "[$START]")"
}

t_started_after_request_is_in_flight() {
  assert_eq "in_flight" "true" "$(in_flight_for "[$REQ,$START]")"
}

t_review_after_start_ends_the_run() {
  assert_eq "in_flight" "false" "$(in_flight_for "[$REQ,$START,$REVIEW]")"
}

t_request_after_start_is_not_in_flight() {
  assert_eq "in_flight" "false" "$(in_flight_for "[$REQ,$START,$LATE_REQ]")"
}

t_another_reviewers_request_is_ignored() {
  assert_eq "in_flight" "true" "$(in_flight_for "[$REQ,$START,$HUMAN_REQ]")"
}

# A request and its run start in the same second: position decides.
t_same_second_start_is_in_flight() {
  local req='{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T18:31:54Z"}'
  local start='{"event":"copilot_work_started","created_at":"2026-09-29T18:31:54Z"}'
  assert_eq "in_flight" "true" "$(in_flight_for "[$req,$start]")"
}

# A re-request in the same second as the run start, listed after it, is newer.
t_same_second_request_after_start_is_not_in_flight() {
  local start='{"event":"copilot_work_started","created_at":"2026-09-29T18:31:54Z"}'
  local req='{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T18:31:54Z"}'
  assert_eq "in_flight" "false" "$(in_flight_for "[$start,$req]")"
}

t_non_object_element_is_ignored() {
  assert_eq "in_flight" "true" "$(in_flight_for "[\"Not Found\",$REQ,$START]")"
}

t_timeline_failure_is_non_zero() {
  local out rc
  out=$(TIMELINE_FAIL=1 copilot_run_in_flight owner repo 1 2>/dev/null)
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "stdout" "" "$out"
}

# `run_suite`, not `main`: keep the driver name distinct from sourced code.
# Progress goes to stderr; stdout carries one JSON result.
run_suite() {
  echo "== copilot-run.sh tests ==" >&2
  run "an empty timeline is not in flight"                 t_empty_timeline_is_not_in_flight
  run "a run start with no request is not in flight"       t_start_without_request_is_not_in_flight
  run "a run started after its request is in flight"       t_started_after_request_is_in_flight
  run "a review after the start ends the run"              t_review_after_start_ends_the_run
  run "a request after the start is not in flight"         t_request_after_start_is_not_in_flight
  run "another reviewer's request is ignored"              t_another_reviewers_request_is_ignored
  run "a same-second run start after its request is in flight" t_same_second_start_is_in_flight
  run "a same-second request after the start is not in flight" t_same_second_request_after_start_is_not_in_flight
  run "a non-object timeline element is ignored"           t_non_object_element_is_ignored
  run "a failed timeline read exits non-zero, no verdict"  t_timeline_failure_is_non_zero
  printf '{"suite":"test_copilot_run.sh","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ $FAIL_COUNT -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
