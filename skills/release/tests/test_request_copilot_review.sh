#!/usr/bin/env bash
# Outcome-based tests for request-copilot-review.sh covering:
#   - fetch_pr_node_id refuses null PR IDs upfront (#42);
#   - discover_copilot_bot_id matches Copilot's login with or without the
#     [bot] suffix — GraphQL Bot.login is inconsistent across contexts (#43);
#   - main() verifies the request from the mutation's OWN returned review
#     requests, not the REST `requested_reviewers` field that omits bot
#     reviewers (#276) — a bot-only reviewer list must verify clean, the case
#     the earlier suite never exercised, which let the REST-verify bug ship;
#   - the mutation runs in union mode (#297) — replace mode reports success and
#     lands nothing for a bot-only request, so only the query TEXT can catch it;
#   - a terminal retry failure surfaces the GraphQL error (#297);
#   - main() removes any pending Copilot request before requesting, and a
#     failed removal stops before any request (#641).
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that returns
# fixture JSON keyed off MOCK_GH_FIXTURE, applying the --jq filter the
# script passes so the filter logic itself is exercised, not just
# duplicated in the test.
#
# Run: bash skills/release/tests/test_request_copilot_review.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/request-copilot-review.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: request-copilot-review.sh not executable at $SCRIPT" >&2; exit 2; }

# Source the script so we can call its helper functions directly. The
# script's `[[ BASH_SOURCE[0] == $0 ]] && main "$@"` guard prevents
# main() from running when sourced (the comparison is false), but the
# guard line itself returns exit code 1 from the failed `[[ ]]`, and
# the script's `set -e` then propagates that 1 back through the source
# operation. Wrap with `|| true` so the outer test driver doesn't get
# nuked by what is effectively the script's idiomatic no-op-when-sourced
# path. After sourcing, also flip errexit back off so per-test
# assertions returning non-zero don't abort the driver.
# shellcheck disable=SC1090  # ShellCheck cannot resolve the dynamically constructed source path.
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
    echo "  pass: $name" >&2
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Mock `gh` — responds to `gh api graphql ... --jq <filter>` by piping
# the fixture (selected by MOCK_GH_FIXTURE) through the requested jq
# filter. This is intentional: the filter is the contract that changed
# in #42 and #43, so the mock applies it the same way the real `gh`
# would. Anything that isn't `gh api graphql` is unsupported here.
gh() {
  if [[ "$1" != "api" || "$2" != "graphql" ]]; then
    echo "mock gh: unsupported invocation: $*" >&2
    return 2
  fi
  local filter=""
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jq) filter="$2"; shift 2 ;;
      *)    shift ;;
    esac
  done
  local fixture
  case "${MOCK_GH_FIXTURE:-}" in
    pr_not_found)
      fixture='{"data":{"repository":{"pullRequest":null}}}' ;;
    pr_found)
      fixture='{"data":{"repository":{"pullRequest":{"id":"PR_kwDOFAKE"}}}}' ;;
    bot_with_suffix)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_withSuffix","login":"copilot-pull-request-reviewer[bot]"}}]}}]}}}}' ;;
    bot_bare_login)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_bareLogin","login":"copilot-pull-request-reviewer"}}]}}]}}}}' ;;
    bot_no_match)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_other","login":"some-other-bot[bot]"}}]}}]}}}}' ;;
    *)
      echo "mock gh: unknown MOCK_GH_FIXTURE='${MOCK_GH_FIXTURE:-}'" >&2
      return 2 ;;
  esac
  if [[ -n "$filter" ]]; then
    echo "$fixture" | jq -r "$filter"
  else
    echo "$fixture"
  fi
}

# A content-aware graphql mock for driving main(): it distinguishes the
# PR-id query, the requestReviews mutation, and the discover query by their
# text, and applies the script's own --jq filter so the real filter logic is
# exercised. Fixtures and behaviour come from env vars the caller sets:
#   MUT_FIXTURE       requestReviews response JSON
#   DISCOVER_FIXTURE  review-history response JSON (bot-id discovery)
#   MUT_FAIL_ONCE + MUT_FAIL_FLAG  fail the FIRST mutation once (stale-ID path)
#   DELETE_FAIL       fail the REST removal with HTTP 403 or 422
#   CALL_LOG          file each call appends its kind to (delete / mutation)
#   TIMELINE_FIXTURE  PR timeline JSON array (default: empty, no run in flight)
#   PENDING_FIXTURE   pending reviewRequests response (default: none pending)
PENDING_NONE='{"data":{"repository":{"pullRequest":{"reviewRequests":{"nodes":[]}}}}}'
PENDING_COPILOT='{"data":{"repository":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}'
# shellcheck disable=SC2329  # invoked indirectly via a per-test gh() override
_main_gh_mock() {
  if [[ "$1" == api && "$2" == --paginate && "$3" == repos/*/issues/*/timeline* ]]; then
    printf '%s' "${TIMELINE_FIXTURE:-[]}"
    return 0
  fi
  if [[ "$1" == api && "$2" == -X && "$3" == DELETE ]]; then
    [[ "$4" == repos/*/pulls/*/requested_reviewers && "$5" == -f && "$6" == 'reviewers[]=Copilot' ]] \
      || { echo "mock gh: unexpected DELETE: $*" >&2; return 2; }
    [[ -n "${CALL_LOG:-}" ]] && echo delete >> "$CALL_LOG"
    case "${DELETE_FAIL:-}" in
      403) echo "gh: Resource not accessible by integration (HTTP 403)" >&2; return 1 ;;
      422) echo "gh: Reviews may only be requested from collaborators. (HTTP 422)" >&2; return 1 ;;
      "")  ;;
      *)   echo "mock gh: unknown DELETE_FAIL='${DELETE_FAIL}'" >&2; return 2 ;;
    esac
    echo '{"number":5,"requested_reviewers":[]}'
    return 0
  fi
  [[ "$1" == api && "$2" == graphql ]] || { echo "mock gh: unsupported: $*" >&2; return 2; }
  local query="" filter=""
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f)   query="${2#query=}"; shift 2 ;;
      --jq) filter="$2"; shift 2 ;;
      *)    shift ;;
    esac
  done
  local fixture
  if [[ "$query" == *requestReviews* ]]; then
    [[ -n "${CALL_LOG:-}" ]] && echo mutation >> "$CALL_LOG"
    # MUT_FAIL_ALWAYS fails BOTH attempts, so the retry path is terminal — the
    # only path whose diagnostic has to carry the GraphQL error text.
    if [[ -n "${MUT_FAIL_ALWAYS:-}" ]]; then
      echo "mock gh: GraphQL: Could not resolve to a node with the global id of 'BOT_stale'" >&2
      return 1
    fi
    if [[ -n "${MUT_FAIL_ONCE:-}" && ! -f "${MUT_FAIL_FLAG:-/nonexistent}" ]]; then
      : > "${MUT_FAIL_FLAG}"
      echo "mock gh: requestReviews rejected the botId" >&2
      return 1
    fi
    fixture="${MUT_FIXTURE:?MUT_FIXTURE unset}"
  elif [[ "$query" == *"reviewRequests(first: 50)"* ]]; then
    fixture="${PENDING_FIXTURE:-$PENDING_NONE}"
  elif [[ "$query" == *"pullRequest(number"* ]]; then
    fixture='{"data":{"repository":{"pullRequest":{"id":"PR_kwDOX"}}}}'
  elif [[ "$query" == *"pullRequests(last"* ]]; then
    fixture="${DISCOVER_FIXTURE:?DISCOVER_FIXTURE unset}"
  else
    echo "mock gh: unrecognized query: $query" >&2; return 2
  fi
  if [[ -n "$filter" ]]; then
    echo "$fixture" | jq -r "$filter"
  else
    echo "$fixture"
  fi
}

# --- test bodies ---

t_fetch_pr_node_id_returns_empty_and_nonzero_on_null_pr() {
  MOCK_GH_FIXTURE=pr_not_found
  local out rc
  out=$(fetch_pr_node_id "owner" "repo" "999" 2>/dev/null)
  rc=$?
  assert_eq "exit code"  "1"  "$rc"  || return 1
  assert_eq "stdout"     ""   "$out" || return 1
}

t_fetch_pr_node_id_refuses_non_numeric_pr_number() {
  # `gh` should never be called when the input is rejected upfront.
  # Override the mock inside a subshell so a future regression where
  # the validation gate is skipped surfaces as a loud test failure
  # without leaking the override into the rest of the test file.
  local err rc
  err=$(
    gh() { echo "mock gh: should not be called for non-numeric input" >&2; return 99; }
    fetch_pr_node_id "owner" "repo" "abc" 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"must be a positive integer"* ]] || { echo "    FAIL: stderr missing 'must be a positive integer': $err" >&2; return 1; }
}

t_fetch_pr_node_id_returns_id_on_real_pr() {
  MOCK_GH_FIXTURE=pr_found
  local out rc
  out=$(fetch_pr_node_id "owner" "repo" "1")
  rc=$?
  assert_eq "exit code"  "0"            "$rc"  || return 1
  assert_eq "stdout"     "PR_kwDOFAKE"  "$out"
}

t_discover_matches_bot_suffix_login() {
  MOCK_GH_FIXTURE=bot_with_suffix
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "id" "BOT_withSuffix" "$out"
}

t_discover_matches_bare_login() {
  MOCK_GH_FIXTURE=bot_bare_login
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "id" "BOT_bareLogin" "$out"
}

t_discover_returns_empty_when_no_copilot_review() {
  MOCK_GH_FIXTURE=bot_no_match
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "stdout" "" "$out"
}

# #276: a request that attaches only a bot reviewer must verify CLEAN. Under
# the old REST verification this list came back `[]` and main() always exited
# 1; verifying from the mutation response fixes it.
t_main_verifies_bot_only_reviewers() {
  local out rc
  out=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}' \
      main owner repo 5 2>/dev/null
  )
  rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  echo "$out" | jq -e '.requested_reviewers | any(test("copilot"; "i"))' >/dev/null 2>&1 \
    || { echo "    FAIL: output envelope missing copilot: $out" >&2; return 1; }
}

# A mutation whose returned reviewers do NOT include Copilot is a real failure.
t_main_fails_when_copilot_absent() {
  local err rc
  err=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"some-other-bot"}}]}}}}}' \
      main owner repo 5 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"not in review requests"* ]] \
    || { echo "    FAIL: stderr missing 'not in review requests': $err" >&2; return 1; }
}

# A rejected pinned bot ID falls back to discovery, then verifies from the
# retried mutation's response.
t_main_falls_back_on_rejected_pinned_id() {
  local out rc flag
  flag=$(mktemp -u)
  out=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FAIL_ONCE=1 MUT_FAIL_FLAG="$flag" \
    DISCOVER_FIXTURE='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_discovered","login":"copilot-pull-request-reviewer"}}]}}]}}}}' \
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}' \
      main owner repo 5 2>/dev/null
  )
  rc=$?
  rm -f "$flag"
  assert_eq "exit code" "0" "$rc" || return 1
  echo "$out" | jq -e '.bot_id == "BOT_discovered"' >/dev/null 2>&1 \
    || { echo "    FAIL: expected the discovered bot id in the output: $out" >&2; return 1; }
}

# #297: without `union: true` the mutation runs in replace mode, which returns
# success and lands nothing for a bot-only request — `reviewRequests` comes
# back empty and the script exits 1 on every invocation against a valid bot ID.
# A mocked response cannot reproduce that, so assert the query TEXT: the mode
# is the contract here, and it is invisible everywhere else.
t_mutation_runs_in_union_mode() {
  local query_file query
  query_file=$(mktemp) || { echo "    FAIL: mktemp failed" >&2; return 1; }
  (
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced request_with_bot_id
    gh() {
      local q=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -f) q="${2#query=}"; shift 2 ;;
          *)  shift ;;
        esac
      done
      printf '%s' "$q" > "$query_file"
      echo '[]'
    }
    request_with_bot_id "PR_kwDOX" "BOT_kgDOCnlnWA" >/dev/null
  )
  query=$(cat "$query_file")
  rm -f "$query_file"
  case "$query" in
    *"union: true"*) return 0 ;;
    *) echo "    FAIL: requestReviews mutation is missing 'union: true': ${query}" >&2; return 1 ;;
  esac
}

# #297: both attempts discarded the mutation's stderr, so a terminal failure
# reported only "request failed with discovered bot ID X" — indistinguishable
# between a rejected bot ID and an auth or network fault. The retry path now
# captures and surfaces the GraphQL error.
t_main_surfaces_mutation_error_on_terminal_failure() {
  local err rc
  err=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FAIL_ALWAYS=1 \
    DISCOVER_FIXTURE='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_discovered","login":"copilot-pull-request-reviewer"}}]}}]}}}}' \
      main owner repo 5 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"Could not resolve to a node"* ]] \
    || { echo "    FAIL: stderr missing the GraphQL error text: $err" >&2; return 1; }
}

MUT_OK_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}'

# Drive main() under the mock with the given env assignments; set RC, OUT,
# ERR and CALLS (the delete/mutation calls in order, space-separated).
run_main() { # <VAR=value>...
  local log errf
  log=$(mktemp) || { echo "    FAIL: mktemp failed" >&2; return 1; }
  errf=$(mktemp) || { rm -f "$log"; echo "    FAIL: mktemp failed" >&2; return 1; }
  OUT=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    env_args=("$@")
    for a in "${env_args[@]}"; do export "${a?}"; done
    CALL_LOG="$log" MUT_FIXTURE="$MUT_OK_FIXTURE" main owner repo 5 2>"$errf"
  )
  RC=$?
  CALLS=$(tr '\n' ' ' < "$log")
  ERR=$(cat "$errf")
  rm -f "$log" "$errf"
}

# A request newer than the last run start never started: the stuck request
# (#641) that turns every re-request into a no-op.
NEVER_STARTED='[{"event":"copilot_work_started","created_at":"2026-09-29T01:40:27Z"},{"event":"reviewed","user":{"login":"Copilot"},"submitted_at":"2026-09-29T01:43:31Z"},{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T13:24:00Z"}]'
IN_FLIGHT='[{"event":"reviewed","user":{"login":"Copilot"},"submitted_at":"2026-09-29T01:43:31Z"},{"event":"review_requested","requested_reviewer":{"login":"Copilot"},"created_at":"2026-09-29T18:31:54Z"},{"event":"copilot_work_started","created_at":"2026-09-29T18:32:51Z"}]'

# #641: a pending request is removed, then requested afresh — in that order.
t_main_removes_pending_request_before_requesting() {
  run_main "PENDING_FIXTURE=$PENDING_COPILOT" "TIMELINE_FIXTURE=$NEVER_STARTED" || return 1
  assert_eq "exit code" "0" "$RC" || return 1
  assert_eq "call order" "delete mutation " "$CALLS"
}

# Nothing pending: no removal is sent, the request goes out.
t_main_requests_without_removal_when_nothing_pending() {
  run_main "PENDING_FIXTURE=$PENDING_NONE" || return 1
  assert_eq "exit code" "0" "$RC" || return 1
  assert_eq "calls" "mutation " "$CALLS" || return 1
  echo "$OUT" | jq -e '.pr_number == 5' >/dev/null 2>&1 \
    || { echo "    FAIL: output envelope missing: $OUT" >&2; return 1; }
}

# A pending request whose removal fails is a real fault, 422 included: exit
# non-zero with the error text, and never send the request that would be a
# silent no-op.
t_main_stops_when_removal_fails_422() {
  run_main "PENDING_FIXTURE=$PENDING_COPILOT" DELETE_FAIL=422 || return 1
  assert_eq "exit code" "1" "$RC" || return 1
  assert_eq "calls" "delete " "$CALLS" || return 1
  [[ "$ERR" == *"HTTP 422"* ]] \
    || { echo "    FAIL: stderr missing the removal error text: $ERR" >&2; return 1; }
}

t_main_stops_when_removal_fails_403() {
  run_main "PENDING_FIXTURE=$PENDING_COPILOT" DELETE_FAIL=403 || return 1
  assert_eq "exit code" "1" "$RC" || return 1
  assert_eq "calls" "delete " "$CALLS" || return 1
  [[ "$ERR" == *"HTTP 403"* ]] \
    || { echo "    FAIL: stderr missing the removal error text: $ERR" >&2; return 1; }
}

# #641: removing the request of a run in progress discards its result, so a
# run in flight is left alone — no removal, no request, exit 0.
t_main_leaves_an_in_flight_run_alone() {
  run_main "PENDING_FIXTURE=$PENDING_COPILOT" "TIMELINE_FIXTURE=$IN_FLIGHT" || return 1
  assert_eq "exit code" "0" "$RC" || return 1
  assert_eq "calls" "" "$CALLS" || return 1
  echo "$OUT" | jq -e '.in_flight == true and .pr_number == 5' >/dev/null 2>&1 \
    || { echo "    FAIL: expected an in_flight envelope: $OUT" >&2; return 1; }
}

# --- driver ---

# `run_suite`, not `main`: the sourced script under test owns `main`.
# Progress goes to stderr; stdout carries one JSON result.
run_suite() {
  echo "== request-copilot-review.sh tests ==" >&2
  run "fetch_pr_node_id refuses null PR with non-zero exit"            t_fetch_pr_node_id_returns_empty_and_nonzero_on_null_pr
  run "fetch_pr_node_id refuses non-numeric pr-number argument"        t_fetch_pr_node_id_refuses_non_numeric_pr_number
  run "fetch_pr_node_id returns ID for a real PR"                      t_fetch_pr_node_id_returns_id_on_real_pr
  run "discover_copilot_bot_id matches Bot.login with [bot] suffix"    t_discover_matches_bot_suffix_login
  run "discover_copilot_bot_id matches Bot.login without suffix"       t_discover_matches_bare_login
  run "discover_copilot_bot_id returns empty when no Copilot review"   t_discover_returns_empty_when_no_copilot_review
  run "main verifies a bot-only reviewer list clean (#276)"           t_main_verifies_bot_only_reviewers
  run "main fails when Copilot is absent from the mutation response"   t_main_fails_when_copilot_absent
  run "main falls back to discovery on a rejected pinned bot ID"       t_main_falls_back_on_rejected_pinned_id
  run "requestReviews mutation runs in union mode (#297)"              t_mutation_runs_in_union_mode
  run "main surfaces the GraphQL error on a terminal failure (#297)"   t_main_surfaces_mutation_error_on_terminal_failure
  run "main removes a pending Copilot request before requesting (#641)" t_main_removes_pending_request_before_requesting
  run "main requests without a removal when nothing is pending (#641)" t_main_requests_without_removal_when_nothing_pending
  run "main stops on a 422 removal of a pending request (#641)"       t_main_stops_when_removal_fails_422
  run "main stops on a 403 removal of a pending request (#641)"       t_main_stops_when_removal_fails_403
  run "main leaves a Copilot run in flight alone (#641)"              t_main_leaves_an_in_flight_run_alone
  printf '{"suite":"test_request_copilot_review.sh","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ $FAIL_COUNT -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
