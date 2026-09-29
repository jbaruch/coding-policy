#!/usr/bin/env bash
# Outcome-based tests for dismiss-stale-reviews.sh.
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that (a) serves a
# fixture reviews list for the GET reviews surface and (b) records every
# dismissal PUT into $DISMISS_LOG. jq runs locally so the script's real
# decision logic is exercised, not duplicated in the test.
#
# Run: bash skills/release/tests/test_dismiss_stale_reviews.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/dismiss-stale-reviews.sh"
[[ -r "$SCRIPT" ]] || { echo "fatal: dismiss-stale-reviews.sh not readable at $SCRIPT" >&2; exit 2; }

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
  DISMISS_LOG="$(mktemp)"
  export DISMISS_LOG
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name" >&2
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
  rm -f "$DISMISS_LOG"
}

# Mock `gh api` — two surfaces:
#   GET  repos/<o>/<r>/pulls/<N>/reviews?per_page=100   -> $MOCK_REVIEWS_BODY
#   PUT  repos/<o>/<r>/pulls/<N>/reviews/<id>/dismissals -> log <id>, return {}
#        (MOCK_PUT_FAIL=1: exit 1 with a parse error on stderr, log nothing)
#   GET  repos/<o>/<r>/pulls/<N>/reviews/<id>           -> state $MOCK_REVIEW_STATE
gh() {
  [[ "$1" == "api" ]] || { echo "mock gh: unsupported invocation: $*" >&2; return 2; }
  shift
  local method="GET" path="" saw_paginate=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -X)         method="$2"; shift 2 ;;
      --paginate) saw_paginate=1; shift ;;
      -f)         shift 2 ;;   # message=... / event=DISMISS
      repos/*)    path="$1"; shift ;;
      *)          shift ;;
    esac
  done

  if [[ "$method" == "PUT" && "$path" == *"/dismissals" ]]; then
    local rid="${path%/dismissals}"; rid="${rid##*/}"
    if [[ "${MOCK_PUT_FAIL:-0}" == "1" ]]; then
      echo "unexpected end of JSON input" >&2
      return 1
    fi
    echo "$rid" >> "$DISMISS_LOG"
    echo '{}'
    return 0
  fi

  # Single-review read-back after a failed PUT: GET .../reviews/<id>.
  if [[ "$path" =~ /reviews/[0-9]+$ ]]; then
    printf '{"id":%s,"state":"%s"}\n' "${path##*/}" "${MOCK_REVIEW_STATE:-CHANGES_REQUESTED}"
    return 0
  fi

  [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: reviews fetch missing --paginate" >&2; return 99; }
  case "$path" in
    *reviews*)  echo "${MOCK_REVIEWS_BODY:-[]}" ;;
    *timeline*) echo "${MOCK_TIMELINE_BODY:-[]}" ;;
    *) echo "mock gh api: unsupported path: $path" >&2; return 2 ;;
  esac
}

# Convenience: how many dismissals were issued this test.
dismiss_count() { [[ -s "$DISMISS_LOG" ]] || { echo 0; return; }; wc -l < "$DISMISS_LOG" | tr -d ' '; }

# --- test bodies ---

# Stale CHANGES_REQUESTED superseded by a later COMMENT from the same bot
# is dismissed. This is the everyday case: bot requests changes, agent
# fixes, bot re-reviews clean with a COMMENT (it cannot APPROVE — 422).
t_stale_cr_then_comment_is_dismissed() {
  MOCK_REVIEWS_BODY='[
    {"id":11,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":12,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out ids n
  out=$(main "owner" "repo" "1") || { echo "    main exited non-zero" >&2; return 1; }
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  n=$(dismiss_count)
  assert_eq "dismissed review_id" "11" "$ids" || return 1
  assert_eq "PUT dismissal count" "1" "$n"     || return 1
  assert_eq "logged dismissed id" "11" "$(tr -d '\n' < "$DISMISS_LOG")"
}

# Latest review is still CHANGES_REQUESTED -> leave it gating, dismiss nothing.
t_latest_cr_is_left_active() {
  MOCK_REVIEWS_BODY='[
    {"id":21,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":22,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":23,"state":"CHANGES_REQUESTED","commit_id":"ccc","submitted_at":"2026-01-03T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out active n
  out=$(main "owner" "repo" "1") || return 1
  active=$(jq -r '.left_active | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  n=$(dismiss_count)
  assert_eq "left_active entry" "github-actions[bot]:23" "$active" || return 1
  assert_eq "no dismissals"     "0"                      "$n"      || return 1
  assert_eq "dismissed empty"   "0" "$(jq '.dismissed | length' <<<"$out")"
}

# An already-DISMISSED stale review is not re-dismissed (idempotent re-run).
t_already_dismissed_is_skipped() {
  MOCK_REVIEWS_BODY='[
    {"id":31,"state":"DISMISSED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":32,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out n
  out=$(main "owner" "repo" "1") || return 1
  n=$(dismiss_count)
  assert_eq "no dismissals" "0" "$n" || return 1
  assert_eq "dismissed empty" "0" "$(jq '.dismissed | length' <<<"$out")"
}

# Per-bot independence: one bot cleared (dismiss its stale CR), the other
# still requesting changes (leave active).
t_per_bot_independence() {
  MOCK_REVIEWS_BODY='[
    {"id":41,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":42,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":43,"state":"CHANGES_REQUESTED","commit_id":"ccc","submitted_at":"2026-01-02T06:00:00Z","user":{"login":"copilot-pull-request-reviewer[bot]"}}
  ]'
  local out dismissed active
  out=$(main "owner" "repo" "1") || return 1
  dismissed=$(jq -r '.dismissed | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  active=$(jq -r '.left_active | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  assert_eq "dismissed codex stale"       "github-actions[bot]:41"                 "$dismissed" || return 1
  assert_eq "copilot left active"         "copilot-pull-request-reviewer[bot]:43"  "$active"    || return 1
  assert_eq "exactly one dismissal"       "1"                                      "$(dismiss_count)"
}

# No reviews at all -> empty envelope, exit 0.
t_no_reviews_is_noop() {
  MOCK_REVIEWS_BODY='[]'
  local out
  out=$(main "owner" "repo" "1") || return 1
  assert_eq "dismissed empty"   "0" "$(jq '.dismissed | length' <<<"$out")"   || return 1
  assert_eq "left_active empty" "0" "$(jq '.left_active | length' <<<"$out")" || return 1
  assert_eq "no dismissals"     "0" "$(dismiss_count)"
}

# Latest review is DISMISSED (not an all-clear) with an EARLIER active
# CHANGES_REQUESTED the bot never cleared -> dismiss nothing. A dismissed
# latest verdict is not a COMMENTED/APPROVED all-clear, so the earlier
# active request must stay put.
t_latest_dismissed_leaves_earlier_active_cr() {
  MOCK_REVIEWS_BODY='[
    {"id":61,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":62,"state":"DISMISSED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out n
  out=$(main "owner" "repo" "1") || return 1
  n=$(dismiss_count)
  assert_eq "no dismissals"   "0" "$n"                                   || return 1
  assert_eq "dismissed empty" "0" "$(jq '.dismissed | length' <<<"$out")"
}

# Latest review DISMISSED by dismiss-ruled-review.sh (marker message) is a
# ruled all-clear: the earlier CHANGES_REQUESTED is swept.
t_latest_ruled_dismissal_sweeps_earlier_cr() {
  MOCK_REVIEWS_BODY='[
    {"id":81,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":82,"state":"DISMISSED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  MOCK_TIMELINE_BODY='[{"event":"review_dismissed","dismissed_review":{"review_id":82,"state":"changes_requested","dismissal_message":"JUDGE-RULED: 0123456789abcdef covers 1 blocking findings at bbb"}}]'
  local out ids
  out=$(main "owner" "repo" "1") || { MOCK_TIMELINE_BODY='[]'; return 1; }
  MOCK_TIMELINE_BODY='[]'
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  assert_eq "earlier CR swept" "81" "$ids" || return 1
  assert_eq "one dismissal"    "1"  "$(dismiss_count)"
}

# A DISMISSED latest whose message lacks the marker stays a no-op.
t_latest_unmarked_dismissal_is_not_all_clear() {
  MOCK_REVIEWS_BODY='[
    {"id":91,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":92,"state":"DISMISSED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  MOCK_TIMELINE_BODY='[{"event":"review_dismissed","dismissed_review":{"review_id":92,"state":"changes_requested","dismissal_message":"dismissed by hand"}}]'
  local out
  out=$(main "owner" "repo" "1") || { MOCK_TIMELINE_BODY='[]'; return 1; }
  MOCK_TIMELINE_BODY='[]'
  assert_eq "no dismissals" "0" "$(dismiss_count)" || return 1
  assert_eq "dismissed empty" "0" "$(jq '.dismissed | length' <<<"$out")"
}

# The fleet App is swept after a ruled dismissal of its latest review, and
# only then: an APPROVED latest leaves its earlier CR to GitHub's own supersession.
t_fleet_reviewer_swept_only_after_ruled_dismissal() {
  MOCK_REVIEWS_BODY='[
    {"id":101,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"coding-policy-fleet-reviewer[bot]"}},
    {"id":102,"state":"DISMISSED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"coding-policy-fleet-reviewer[bot]"}}
  ]'
  MOCK_TIMELINE_BODY='[{"event":"review_dismissed","dismissed_review":{"review_id":102,"state":"changes_requested","dismissal_message":"JUDGE-RULED: 0123456789abcdef covers 1 blocking findings at bbb; tracked in #12"}}]'
  local out ids
  out=$(main "owner" "repo" "1") || { MOCK_TIMELINE_BODY='[]'; return 1; }
  MOCK_TIMELINE_BODY='[]'
  ids=$(jq -r '.dismissed | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  assert_eq "fleet CR swept" "coding-policy-fleet-reviewer[bot]:101" "$ids" || return 1
  : > "$DISMISS_LOG"
  MOCK_REVIEWS_BODY='[
    {"id":111,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"coding-policy-fleet-reviewer[bot]"}},
    {"id":112,"state":"APPROVED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"coding-policy-fleet-reviewer[bot]"}}
  ]'
  out=$(main "owner" "repo" "1") || return 1
  assert_eq "approved fleet review sweeps nothing" "0" "$(dismiss_count)" || return 1
  assert_eq "fleet not left_active" "0" "$(jq '.left_active | length' <<<"$out")"
}

# Two stale CRs before a clean COMMENT -> both dismissed.
t_multiple_stale_crs_all_dismissed() {
  MOCK_REVIEWS_BODY='[
    {"id":51,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":52,"state":"CHANGES_REQUESTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":53,"state":"COMMENTED","commit_id":"ccc","submitted_at":"2026-01-03T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out ids
  out=$(main "owner" "repo" "1") || return 1
  ids=$(jq -r '.dismissed | map(.review_id) | sort | join(",")' <<<"$out")
  assert_eq "both stale dismissed" "51,52" "$ids" || return 1
  assert_eq "two dismissals"       "2"     "$(dismiss_count)"
}

# The bot logins contain `[bot]`, a glob bracket. If the loop iterated them
# unquoted, a file in the working directory matching the pattern (e.g.
# `github-actionsb`) would rewrite the login token via pathname expansion
# and the review would never be found. Run from a directory seeded with
# such a decoy file and assert the dismissal still happens.
t_login_is_glob_safe_against_cwd_files() {
  MOCK_REVIEWS_BODY='[
    {"id":71,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":72,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local decoy_dir out ids
  decoy_dir=$(mktemp -d)
  # Files that `github-actions[bot]` and `copilot-...[bot]` would glob to.
  : > "${decoy_dir}/github-actionsb"
  : > "${decoy_dir}/copilot-pull-request-reviewero"
  out=$(cd "$decoy_dir" && main "owner" "repo" "1") || { rm -rf "$decoy_dir"; return 1; }
  rm -rf "$decoy_dir"
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  assert_eq "dismissed despite cwd decoy files" "71" "$ids" || return 1
  assert_eq "one dismissal" "1" "$(dismiss_count)"
}

# PUT exits non-zero on an unparseable response after GitHub already
# dismissed the review (#647): the read-back says DISMISSED, so the
# dismissal counts, exit 0, with a warning on stderr.
t_failed_put_but_dismissed_is_success() {
  MOCK_REVIEWS_BODY='[
    {"id":121,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":122,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out rc err_file err ids
  err_file=$(mktemp) || { echo "    mktemp failed" >&2; return 1; }
  out=$(MOCK_PUT_FAIL=1 MOCK_REVIEW_STATE=DISMISSED main "owner" "repo" "1" 2>"$err_file")
  rc=$?
  err=$(<"$err_file"); rm -f "$err_file"
  assert_eq "exit code" "0" "$rc" || return 1
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  assert_eq "dismissed review_id" "121" "$ids" || return 1
  [[ "$err" == *"warning: dismissal response for review 121"* ]] \
    || { echo "    FAIL: no parse warning on stderr: ${err}" >&2; return 1; }
}

# PUT exits non-zero and the review still reads CHANGES_REQUESTED: failure.
t_failed_put_not_dismissed_is_failure() {
  MOCK_REVIEWS_BODY='[
    {"id":131,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":132,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local rc err_file err
  err_file=$(mktemp) || { echo "    mktemp failed" >&2; return 1; }
  (MOCK_PUT_FAIL=1 MOCK_REVIEW_STATE=CHANGES_REQUESTED main "owner" "repo" "1" >/dev/null 2>"$err_file")
  rc=$?
  err=$(<"$err_file"); rm -f "$err_file"
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"unexpected end of JSON input"* ]] \
    || { echo "    FAIL: PUT error not surfaced: ${err}" >&2; return 1; }
}

# --- runner ---

# `run_suite`, not `main`: the sourced script under test owns `main`.
# Progress goes to stderr; stdout carries one JSON result.
run_suite() {
  echo "test_dismiss_stale_reviews.sh" >&2
  run "stale CR then COMMENT is dismissed"        t_stale_cr_then_comment_is_dismissed
  run "latest CR is left active"                  t_latest_cr_is_left_active
  run "already-dismissed is skipped"              t_already_dismissed_is_skipped
  run "per-bot independence"                      t_per_bot_independence
  run "no reviews is a no-op"                     t_no_reviews_is_noop
  run "latest DISMISSED leaves earlier active CR" t_latest_dismissed_leaves_earlier_active_cr
  run "latest ruled dismissal sweeps earlier CR"  t_latest_ruled_dismissal_sweeps_earlier_cr
  run "unmarked dismissal is not an all-clear"    t_latest_unmarked_dismissal_is_not_all_clear
  run "fleet reviewer swept only after ruling"    t_fleet_reviewer_swept_only_after_ruled_dismissal
  run "multiple stale CRs all dismissed"          t_multiple_stale_crs_all_dismissed
  run "login is glob-safe against cwd files"      t_login_is_glob_safe_against_cwd_files
  run "failed PUT but DISMISSED is success"       t_failed_put_but_dismissed_is_success
  run "failed PUT not dismissed is failure"       t_failed_put_not_dismissed_is_failure
  printf '{"suite":"test_dismiss_stale_reviews.sh","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ $FAIL_COUNT -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
