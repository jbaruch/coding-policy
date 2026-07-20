#!/usr/bin/env bash
# Tests for fleet-poll.sh — the "which open PRs still need review" enumeration.
# `gh` is mocked with canned API responses, so no network. Deterministic,
# hermetic (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_fleet_poll.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/fleet-poll.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: fleet-poll.sh not readable at $SCRIPT" >&2; exit 2; }

# shellcheck source=/dev/null
source "$SCRIPT"
set +e

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

MOCK_DIR=""
declare -a MOCK_REPOS=()

# Mock gh: serve installation repos, per-repo open PRs, and per-PR reviews from
# fixture files written by each test. Routes on the first /-prefixed argument.
gh() {
  local a url=""
  for a in "$@"; do case "$a" in /*) url="$a"; break;; esac; done
  case "$url" in
    /installation/repositories)
      printf '%s\n' "${MOCK_REPOS[@]}"
      ;;
    */pulls\?state=open*)
      local rf="${url#/repos/}"; rf="${rf%%/pulls*}"
      cat "${MOCK_DIR}/${rf//\//__}.pulls.json"
      ;;
    */reviews)
      local rest="${url#/repos/}" rf n f
      rf="${rest%%/pulls/*}"; n="${rest#*/pulls/}"; n="${n%/reviews}"
      f="${MOCK_DIR}/${rf//\//__}.reviews.${n}.json"
      if [[ -f "$f" ]]; then cat "$f"; else echo '[]'; fi
      ;;
    *) return 1 ;;
  esac
}

run_main() { ( set -euo pipefail; REVIEWER_LOGIN="app[bot]" main ); }

setup() { MOCK_DIR=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }; MOCK_REPOS=(); }
teardown() { rm -rf "$MOCK_DIR" || echo "test_fleet_poll: warning: could not remove ${MOCK_DIR}" >&2; }

# Guarded fixture write: reads a heredoc on stdin and aborts loudly on a failed
# write, so a broken fixture can never let a test pass for the wrong reason
# (rules/error-handling.md aggregate carve-out — setup steps carry their own check).
writef() { cat > "$1" || { echo "fatal: could not write fixture $1" >&2; exit 2; }; }

# --- an un-reviewed same-repo PR is included ---
t_needs_review() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  local out; out=$(run_main)
  [[ "$(jq 'length' <<<"$out")" == "1" ]]                    || { bad "needs_review: one entry ($out)"; teardown; return; }
  [[ "$(jq -r '.[0].number'   <<<"$out")" == "7" ]]          || { bad "needs_review: number 7 ($out)"; teardown; return; }
  [[ "$(jq -r '.[0].base_ref' <<<"$out")" == "main" ]]       || { bad "needs_review: base main ($out)"; teardown; return; }
  [[ "$(jq -r '.[0].head_sha' <<<"$out")" == "aaa111" ]]     || { bad "needs_review: head aaa111 ($out)"; teardown; return; }
  ok "un-reviewed same-repo PR is included"; teardown
}

# --- a PR already reviewed at its current head SHA is skipped ---
t_already_reviewed() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"COMMENTED"}]
JSON
  local out; out=$(run_main)
  if [[ "$(jq 'length' <<<"$out")" == "0" ]]; then ok "PR reviewed at current head is skipped"; else bad "already_reviewed: expected empty ($out)"; fi
  teardown
}

# --- a PR reviewed only at an OLD head SHA (head advanced) is included ---
t_head_advanced() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"bbb222","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"COMMENTED"}]
JSON
  local out; out=$(run_main)
  if [[ "$(jq 'length' <<<"$out")" == "1" ]]; then ok "PR whose head advanced past the last review is included"; else bad "head_advanced: expected one ($out)"; fi
  teardown
}

# --- a fork PR is skipped (App token cannot fetch a fork head) ---
t_fork_skipped() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"someone/repo-a-fork"}}}]
JSON
  local out; out=$(run_main)
  if [[ "$(jq 'length' <<<"$out")" == "0" ]]; then ok "fork PR is skipped"; else bad "fork_skipped: expected empty ($out)"; fi
  teardown
}

# --- a review by a DIFFERENT login at the head SHA does not count ---
t_other_reviewer_ignored() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"user":{"login":"copilot[bot]"},"commit_id":"aaa111","state":"COMMENTED"}]
JSON
  local out; out=$(run_main)
  if [[ "$(jq 'length' <<<"$out")" == "1" ]]; then ok "another reviewer's verdict does not mark it reviewed"; else bad "other_reviewer: expected one ($out)"; fi
  teardown
}

# --- multiple repos, mixed states ---
t_multi_repo() {
  setup; MOCK_REPOS=(jbaruch/repo-a jbaruch/repo-b)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"COMMENTED"}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-b.pulls.json" <<'JSON'
[{"number":3,"base":{"ref":"trunk"},"head":{"sha":"ccc333","repo":{"full_name":"jbaruch/repo-b"}}}]
JSON
  local out; out=$(run_main)
  [[ "$(jq 'length' <<<"$out")" == "1" ]]                 || { bad "multi_repo: one entry ($out)"; teardown; return; }
  [[ "$(jq -r '.[0].repo' <<<"$out")" == "repo-b" ]]      || { bad "multi_repo: entry is repo-b ($out)"; teardown; return; }
  ok "across repos, only the un-reviewed PR surfaces"; teardown
}

echo "== fleet-poll.sh tests =="
t_needs_review
t_already_reviewed
t_head_advanced
t_fork_skipped
t_other_reviewer_ignored
t_multi_repo
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
