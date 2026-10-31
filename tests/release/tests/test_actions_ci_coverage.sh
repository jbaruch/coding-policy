#!/usr/bin/env bash
# Real poll -> watcher outcomes with synthetic, PR-associated Actions metadata.
# Optional script argument replays the same cases against an older poll owner.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
POLL="${1:-$ROOT/skills/release/poll-pr-reviews.sh}"
WATCH="$ROOT/skills/release/watch-pr-reviews.sh"
HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
OTHER_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
PASS_COUNT=0
FAIL_COUNT=0
CHECKS='[{"name":"label","bucket":"pass"},{"name":"trigger","bucket":"pass"}]'
RUNS='{"total_count":0,"workflow_runs":[]}'
API_ERROR=0

gh() {
  case "$1 $2" in
    'pr checks') printf '%s\n' "$CHECKS" ;;
    'pr view') jq -n --arg h "$HEAD_SHA" '{mergeStateStatus:"CLEAN",mergeable:"MERGEABLE",headRefOid:$h}' ;;
    'api --paginate')
      case "$3" in
        "repos/demo/repo/actions/runs?event=pull_request&head_sha=$HEAD_SHA&per_page=100")
          if [[ "$API_ERROR" != 0 ]]; then
            echo "fixture: Actions read unavailable (HTTP $API_ERROR)" >&2
            return 1
          fi
          printf '%s\n' "$RUNS"
          ;;
        *'/reviews?per_page=100')
          jq -n --arg h "$HEAD_SHA" '[
            {id:1,user:{login:"coding-policy-fleet-reviewer[bot]"},state:"APPROVED",commit_id:$h,submitted_at:"2020-01-01T01:00:00Z",body:"Policy clear"},
            {id:2,user:{login:"jbaruch-claude-reviewer[bot]"},state:"APPROVED",commit_id:$h,submitted_at:"2020-01-01T01:00:00Z",body:"<!-- coding-policy:claude-quality-review:v1 --> Quality clear"}]'
          ;;
        *'/comments?per_page=100') echo '[]' ;;
        *) echo "fixture: unexpected API route: $3" >&2; return 2 ;;
      esac
      ;;
    *) echo "fixture: unexpected gh invocation: $*" >&2; return 2 ;;
  esac
}

poll_snapshot() (
  # shellcheck disable=SC1090  # Test-selected current/old owner, never provider code.
  source "$POLL" || true
  set +e
  main demo repo 42
)

watch_result() (
  export WATCH_PR_REVIEWS_INTERVAL_SEC=1 WATCH_PR_REVIEWS_BUDGET_SEC=1
  # shellcheck disable=SC1090  # Fixed sibling owner, tested independently too.
  source "$WATCH" || true
  set +e
  # shellcheck disable=SC2329  # Invoked by the sourced watcher.
  fetch_snapshot() { poll_snapshot; }
  # shellcheck disable=SC2329  # No wall clock dependence in these outcome tests.
  sleep() { return 0; }
  main demo repo 42
)

record() {
  jq -n --arg h "$HEAD_SHA" --arg status "$1" --arg conclusion "$2" '
    {id:101,workflow_id:12,path:".github/workflows/ci.yml",run_number:8,run_attempt:2,
      repository:{full_name:"demo/repo"},event:"pull_request",head_sha:$h,
      status:$status,conclusion:(if $conclusion=="null" then null else $conclusion end),
      pull_requests:[{number:42,url:"https://api.github.com/repos/demo/repo/pulls/42",
        head:{sha:$h},base:{repo:{url:"https://api.github.com/repos/demo/repo"}}}]}'
}

inventory() { jq -s '{total_count:length,workflow_runs:.}'; }
expect_watch() {
  local want="$1" rc_want="$2" out rc=0
  out=$(watch_result) || rc=$?
  [[ "$rc" == "$rc_want" ]] || { echo "expected rc $rc_want, got $rc" >&2; return 1; }
  [[ "$(jq -r '.watch.result' <<<"$out")" == "$want" ]]
}

pending_attempt_vetoes_visible_success() {
  local RUNS
  RUNS=$(record in_progress null | inventory) || return 1
  expect_watch pending_at_budget 1
}
successful_attempt_retains_ready() {
  local RUNS out
  RUNS=$(record completed success | inventory) || return 1
  expect_watch ready 0 || return 1
  out=$(poll_snapshot) || return 1
  [[ "$(jq -c '.ci.actions.runs[0] | [.workflow_id,.run_number,.run_attempt]' <<<"$out")" == '[12,8,2]' ]]
}
failed_attempt_vetoes_visible_success() {
  local RUNS
  RUNS=$(record completed failure | inventory) || return 1
  expect_watch ci_failure 0
}
unrelated_runs_do_not_gate() {
  local RUNS
  RUNS=$(record in_progress null | jq --arg h "$OTHER_SHA" '.head_sha=$h' | inventory) || return 1
  expect_watch ready 0 || return 1
  RUNS=$(record in_progress null | jq '.pull_requests[0].number=99 | .pull_requests[0].url="https://api.github.com/repos/demo/repo/pulls/99"' | inventory) || return 1
  expect_watch ready 0 || return 1
  RUNS=$(record in_progress null | jq '.event="workflow_dispatch"' | inventory) || return 1
  expect_watch ready 0
}
api_failure_never_means_no_ci() {
  local API_ERROR CHECKS='[]' out rc
  for API_ERROR in 403 404; do
    rc=0
    out=$(watch_result) || rc=$?
    [[ "$rc" == 2 && -z "$out" ]] || return 1
  done
}
genuine_no_ci_retains_none() {
  local CHECKS='[]' RUNS='{"total_count":0,"workflow_runs":[]}' out
  out=$(poll_snapshot) || return 1
  [[ "$(jq -r '.ci.status' <<<"$out")" == none ]] || return 1
  expect_watch ready 0
}
new_workflow_run_supersedes_cancelled_run() {
  local RUNS old new
  old=$(record completed cancelled | jq '.id=100 | .run_number=7 | .run_attempt=1') || return 1
  new=$(record completed success) || return 1
  RUNS=$(printf '%s\n%s' "$old" "$new" | inventory) || return 1
  expect_watch ready 0 || return 1
  RUNS=$(record completed cancelled | inventory) || return 1
  expect_watch pending_at_budget 1
}
visible_failure_and_cancel_are_preserved() {
  local RUNS CHECKS
  RUNS=$(record completed success | inventory) || return 1
  CHECKS='[{"name":"build","bucket":"fail"}]'
  expect_watch ci_failure 0 || return 1
  CHECKS='[{"name":"other-job","bucket":"cancel"}]'
  expect_watch pending_at_budget 1
}
ambiguous_and_incomplete_metadata_refuse() {
  local RUNS out rc row
  row=$(record completed success) || return 1
  for RUNS in \
    '{"total_count":1,"workflow_runs":[]}' \
    '{"total_count":0,"workflow_runs":[null]}' \
    "$(printf '%s' "$row" | jq '.pull_requests=[]' | inventory)" \
    "$(printf '%s' "$row" | jq --arg h "$OTHER_SHA" '.pull_requests[0].head.sha=$h' | inventory)" \
    "$(printf '%s' "$row" | jq '.pull_requests[0].base.repo.url="https://api.github.com/repos/foreign/repo"' | inventory)" \
    "$(printf '%s' "$row" | jq '.path=".github/workflows/ci.yml@unbound"' | inventory)" \
    "$(printf '%s\n%s' "$row" "$row" | inventory)"; do
    rc=0
    out=$(watch_result) || rc=$?
    [[ "$rc" == 2 && -z "$out" ]] || return 1
  done
}
complete_pages_preserve_later_pending_workflow() {
  local RUNS a b
  a=$(record completed success) || return 1
  b=$(record in_progress null | jq '.id=102 | .workflow_id=13 | .path=".github/workflows/verify.yml"') || return 1
  RUNS=$(printf '%s\n%s' "$a" "$b" | jq -cs '.[] | {total_count:2,workflow_runs:[.]}') || return 1
  expect_watch pending_at_budget 1
}
run_case() {
  if "$1"; then PASS_COUNT=$((PASS_COUNT+1)); echo "pass: $1" >&2
  else FAIL_COUNT=$((FAIL_COUNT+1)); echo "FAIL: $1" >&2; fi
}
run_suite() {
  local test_case
  for test_case in pending_attempt_vetoes_visible_success successful_attempt_retains_ready \
    failed_attempt_vetoes_visible_success unrelated_runs_do_not_gate api_failure_never_means_no_ci \
    genuine_no_ci_retains_none new_workflow_run_supersedes_cancelled_run \
    visible_failure_and_cancel_are_preserved ambiguous_and_incomplete_metadata_refuse \
    complete_pages_preserve_later_pending_workflow; do
    run_case "$test_case"
  done
  printf '{"suite":"actions_ci_coverage","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ "$FAIL_COUNT" == 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
