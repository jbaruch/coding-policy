#!/usr/bin/env bash
# Outcome-based tests for request-copilot-review.sh focused on the two
# behaviours #42 + #43 fixed: fetch_pr_node_id refuses null PR IDs
# upfront, and discover_copilot_bot_id matches Copilot's login with or
# without the [bot] suffix (GraphQL Bot.login is inconsistent across
# contexts).
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

# --- driver ---

echo "== request-copilot-review.sh tests =="
run "fetch_pr_node_id refuses null PR with non-zero exit"            t_fetch_pr_node_id_returns_empty_and_nonzero_on_null_pr
run "fetch_pr_node_id refuses non-numeric pr-number argument"        t_fetch_pr_node_id_refuses_non_numeric_pr_number
run "fetch_pr_node_id returns ID for a real PR"                      t_fetch_pr_node_id_returns_id_on_real_pr
run "discover_copilot_bot_id matches Bot.login with [bot] suffix"    t_discover_matches_bot_suffix_login
run "discover_copilot_bot_id matches Bot.login without suffix"       t_discover_matches_bare_login
run "discover_copilot_bot_id returns empty when no Copilot review"   t_discover_returns_empty_when_no_copilot_review

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
