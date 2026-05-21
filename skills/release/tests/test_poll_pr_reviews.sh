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
      local subcmd="$2"
      shift 2
      case "$subcmd" in
        view)
          # Contract: `gh pr view <N> --repo <o/r> --json mergeStateStatus,mergeable`.
          # Validate the --json args explicitly so a regression that drops --json
          # or asks for the wrong fields surfaces as a loud mock failure rather
          # than passing silently against a permissive stub.
          local saw_json=0 json_args=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              --json) saw_json=1; json_args="${2:-}"; shift 2 ;;
              *)      shift ;;
            esac
          done
          [[ $saw_json -eq 1 ]] || { echo "mock gh pr view: missing --json flag (contract: --json mergeStateStatus,mergeable)" >&2; return 99; }
          [[ "$json_args" == "mergeStateStatus,mergeable" ]] || { echo "mock gh pr view: wrong --json args: '${json_args}' (expected 'mergeStateStatus,mergeable')" >&2; return 99; }
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
        *) echo "mock gh pr: unsupported subcommand: $subcmd" >&2; return 2 ;;
      esac
      ;;
    api)
      # gh api --paginate repos/<o>/<r>/pulls/<N>/reviews?per_page=100
      # gh api --paginate repos/<o>/<r>/pulls/<N>/comments?per_page=100
      # The script pipes the raw paginated output through `jq -s` itself,
      # so this mock no longer forwards `--jq`. It echoes a fixture body
      # keyed off the path; tests can simulate multiple pages by setting
      # MOCK_REVIEWS_BODY / MOCK_COMMENTS_BODY to several concatenated
      # JSON arrays (what `gh api --paginate` actually emits across pages).
      shift  # consume "api"
      local path="" saw_paginate=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --paginate) saw_paginate=1; shift ;;
          --jq)       echo "mock gh api: --jq is incompatible with --paginate here; the script should jq -s externally" >&2; return 99 ;;
          *)          [[ -z "$path" ]] && path="$1"; shift ;;
        esac
      done
      [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: missing --paginate (required so the script never silently misses page 2+)" >&2; return 99; }
      case "$path" in
        *reviews*)  echo "${MOCK_REVIEWS_BODY:-[]}" ;;
        *comments*) echo "${MOCK_COMMENTS_BODY:-[]}" ;;
        *) echo "mock gh api: unsupported path: $path" >&2; return 2 ;;
      esac
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

# Issue #83: on PRs with > 1 page of reviews, gh api without --paginate
# returns only page 1. The pre-fix `| last` filter then picked the last
# entry on page 1 — not the actual newest review on the last page — and
# the gate could approve a merge against stale data.
#
# Build a fixture that mimics what `gh api --paginate` actually emits:
# two concatenated JSON arrays. Page 1's last entry is a COMMENTED review
# at 17:00; page 2's last entry is a CHANGES_REQUESTED review at 18:04.
# A correct implementation must report CHANGES_REQUESTED@18:04.
t_latest_review_by_picks_from_last_page() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-05-18T16:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-05-18T17:00:00Z"}][{"user":{"login":"github-actions[bot]"},"state":"CHANGES_REQUESTED","submitted_at":"2026-05-18T18:04:00Z"}]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "state from last page"        "CHANGES_REQUESTED"     "$state"        || return 1
  assert_eq "submitted_at from last page" "2026-05-18T18:04:00Z"  "$submitted_at"
}

t_latest_review_by_returns_none_when_no_reviews() {
  MOCK_REVIEWS_BODY='[]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "state for empty"        "none" "$state"        || return 1
  assert_eq "submitted_at for empty" "null" "$submitted_at"
}

t_latest_review_by_filters_other_logins_across_pages() {
  # Page 1: two human reviews + one bot review. Page 2: one human review
  # that's newer than the bot review. The bot's latest is still the page-1
  # bot review, even though the page-2 human is newer.
  MOCK_REVIEWS_BODY='[{"user":{"login":"alice"},"state":"COMMENTED","submitted_at":"2026-05-18T15:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-05-18T16:00:00Z"},{"user":{"login":"bob"},"state":"COMMENTED","submitted_at":"2026-05-18T16:30:00Z"}][{"user":{"login":"alice"},"state":"COMMENTED","submitted_at":"2026-05-18T17:00:00Z"}]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "bot state"        "APPROVED"             "$state"        || return 1
  assert_eq "bot submitted_at" "2026-05-18T16:00:00Z" "$submitted_at"
}

# Same shape for comments: counts must sum across pages, not pick page 1
# alone. Mix in a non-target login and an in_reply_to_id to confirm the
# filter still discards both.
t_toplevel_comments_by_sums_across_pages() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"alice"},"in_reply_to_id":null}][{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":12345}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "github-actions[bot]")
  assert_eq "top-level bot comments across both pages" "3" "$count"
}

t_toplevel_comments_by_returns_zero_for_no_comments() {
  MOCK_COMMENTS_BODY='[]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "github-actions[bot]")
  assert_eq "comments count for empty" "0" "$count"
}

# --- driver ---

echo "== poll-pr-reviews.sh tests =="
run "fetch_merge_state returns {CLEAN, MERGEABLE} for a clean PR"     t_fetch_merge_state_clean_returns_mergeable_envelope
run "fetch_merge_state returns {DIRTY, CONFLICTING} on conflict"      t_fetch_merge_state_dirty_returns_conflicting_envelope
run "fetch_merge_state propagates UNKNOWN/UNKNOWN while computing"    t_fetch_merge_state_unknown_returns_unknown_envelope
run "main surfaces merge_state as a top-level field"                  t_main_surfaces_merge_state_as_top_level_field
run "main propagates DIRTY merge_state end-to-end"                    t_main_propagates_dirty_state
run "latest_review_by picks newest review on page 2 (issue #83)"      t_latest_review_by_picks_from_last_page
run "latest_review_by returns 'none' for empty reviews"               t_latest_review_by_returns_none_when_no_reviews
run "latest_review_by ignores other logins across pages"              t_latest_review_by_filters_other_logins_across_pages
run "toplevel_comments_by sums counts across pages (issue #83)"       t_toplevel_comments_by_sums_across_pages
run "toplevel_comments_by returns 0 for empty comments"               t_toplevel_comments_by_returns_zero_for_no_comments

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
