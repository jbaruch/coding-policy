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

OBSERVED_REVIEW=$(python3 "${SCRIPT%/.github/*}/tests/fixtures/quality_review_5962464346.py") || exit 2

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

MOCK_DIR=""
declare -a MOCK_REPOS=()
MOCK_ARCHIVED_REPOS='[]'
MOCK_DISABLED_REPOS='[]'

# Mock gh: serve installation repos, per-repo open PRs, and per-PR reviews from
# fixture files written by each test. Routes on the first /-prefixed argument.
gh() {
  local a url="" query="" previous=""
  for a in "$@"; do case "$a" in /*|repos/*) url="$a"; break;; esac; done
  for a in "$@"; do
    if [[ "$previous" == --jq ]]; then query="$a"; fi
    previous="$a"
  done
  printf '%s\n' "$url" >> "$MOCK_DIR/requests.log" || return 1
  case "$url" in
    /installation/repositories)
      local metadata
      metadata=$(printf '%s\n' "${MOCK_REPOS[@]}" | jq -Rn \
        --argjson archived "$MOCK_ARCHIVED_REPOS" --argjson disabled "$MOCK_DISABLED_REPOS" \
        '{repositories: [inputs | select(length > 0) | . as $name | {full_name: $name,
          archived: ($archived | index($name) != null),
          disabled: ($disabled | index($name) != null)}]}') || return 1
      jq -r "$query" <<< "$metadata"
      ;;
    */contents/.github/fleet-review-enabled)
      # Marker present by default; a <repo>.unmarked fixture makes it a 404, so
      # existing tests need no change and only the opt-out case is set explicitly.
      local rf="${url#/repos/}"; rf="${rf%%/contents*}"
      if [[ -f "${MOCK_DIR}/${rf//\//__}.unmarked" ]]; then
        echo "gh: Not Found (HTTP 404)" >&2
        return 1
      fi
      return 0
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
    repos/*/issues/*/timeline*)
      local rest="${url#repos/}" rf n f
      rf="${rest%%/issues/*}"; n="${rest#*/issues/}"; n="${n%%/*}"
      f="${MOCK_DIR}/${rf//\//__}.timeline.${n}.json"
      if [[ -f "$f" ]]; then cat "$f"; else echo '[]'; fi
      ;;
    *) return 1 ;;
  esac
}

run_main() { ( set -euo pipefail; REVIEWER_LOGIN="${1:-app[bot]}" main ); }

setup() {
  MOCK_DIR=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
  MOCK_REPOS=(); MOCK_ARCHIVED_REPOS='[]'; MOCK_DISABLED_REPOS='[]'
}
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

# --- a repo without the opt-in marker is skipped entirely ---
t_unmarked_repo_skipped() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  : > "$MOCK_DIR/jbaruch__repo-a.unmarked"   # 404 on the marker check
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  local out; out=$(run_main)
  if [[ "$(jq 'length' <<<"$out")" == "0" ]]; then ok "repo without the fleet-review marker is skipped"; else bad "unmarked: expected empty ($out)"; fi
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

t_lanes_deduplicate_separately() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  local out
  local reviews="$MOCK_DIR/jbaruch__repo-a.reviews.7.json"
  jq '[{number:7,base:{ref:"main"},head:{sha:.commit_id,repo:{full_name:"jbaruch/repo-a"}}}]' <<<"$OBSERVED_REVIEW" > "$MOCK_DIR/jbaruch__repo-a.pulls.json" || return 1
  local history mutation
  history=$(jq '[. + {id:9, body:("<!-- coding-policy:claude-quality-review:v1 -->\nReviewed commit: " +
    .commit_id + ". Code quality and bugs only.\n")}, .]' <<<"$OBSERVED_REVIEW") || return 1
  local head state
  head=$(jq -r .commit_id <<<"$OBSERVED_REVIEW") || return 1
  state=$(latest_live_quality_state "$head" <<<"$history") || return 1
  [[ "$state" == none ]] || { bad "sourced callers expect bare none"; teardown; return; }
  state=$(bash "$REVIEW_MODULE_DIR/review-lanes.sh" state "$head" <<<"$history") || return 1
  [[ "$state" == '"none"' ]] || { bad "executable callers expect a JSON string"; teardown; return; }
  for mutation in '.' 'reverse' '.[1].body=null' '.[1].body="missing marker"' '.[1].body=.[0].body | .[1].state="DISMISSED"'; do
    jq "$mutation" <<<"$history" > "$reviews" || return 1
    out=$(REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN") || return 1
    [[ "$(jq length <<<"$out")" == 1 ]] || { bad "invalid latest quality review must re-enter the worklist ($mutation)"; teardown; return; }
  done
  jq '. + [(.[0] + {id:5962464347, state:"CHANGES_REQUESTED"}), (.[1] + {id:5962464348, state:"PENDING", submitted_at:null})]' \
    <<<"$history" > "$reviews" || return 1
  out=$(REVIEW_LANE=policy run_main "$CLAUDE_REVIEW_LOGIN")
  [[ "$(jq length <<<"$out")" == 1 ]] || { bad "quality cannot suppress missing policy"; teardown; return; }
  out=$(REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN")
  [[ "$(jq length <<<"$out")" == 0 ]] || { bad "fresh quality change request must suppress duplicate work"; teardown; return; }
  ok "policy and quality deduplicate separately"; teardown
}

t_bad_quality_history_isolated_to_one_pr() {
  setup; MOCK_REPOS=(jbaruch/healthy jbaruch/affected)
  local repo mutation bound copy order out err expected
  for repo in healthy affected; do
    jq --arg repo "jbaruch/$repo" '[{number:7,base:{ref:"main"},head:{sha:.commit_id,repo:{full_name:$repo}}}]' \
      <<<"$OBSERVED_REVIEW" > "$MOCK_DIR/jbaruch__${repo}.pulls.json" || return 1
  done
  bound=$(jq -c '[.body |= sub("07ff68f99b0a1acbb5b7af20532eb255b06d34f8"; "d8b9a1e0ea8706cf7ed95f04ca08092ae3d839ca")]' <<<"$OBSERVED_REVIEW") || return 1
  for mutation in copies missing_actor lookalike decimal string time date row omitted login; do
    expected="Repeated native review ID"
    case "$mutation" in
      copies) copy=$(jq -c '[.]' <<<"$OBSERVED_REVIEW") || return 1 ;;
      missing_actor) copy=$(jq -c '[.user = null]' <<<"$OBSERVED_REVIEW") || return 1 ;;
      lookalike) copy=$(jq -c '[.user.login = "similar-reviewer[bot]"]' <<<"$OBSERVED_REVIEW") || return 1 ;;
      row) copy='[null]'; expected="Invalid native review row" ;;
      omitted) copy='[{"id":7}]'; expected="Missing review attribution" ;;
      login) copy='[{"id":7,"user":{}}]'; expected="Invalid review attribution" ;;
      decimal)
        # Preserve the raw decimal token across jq versions.
        copy=$(jq -c '[.user.login = "similar-reviewer[bot]"]' <<<"$OBSERVED_REVIEW" | sed 's/"id":5962464346/"id":5962464346.0/') || return 1
        expected="Invalid native review ID" ;;
      string)
        copy=$(jq -c '[.user = null | .id |= tostring]' <<<"$OBSERVED_REVIEW") || return 1
        expected="Invalid native review ID" ;;
      time)
        copy=$(jq -c '[.id += 1 | .submitted_at = null]' <<<"$OBSERVED_REVIEW") || return 1
        expected="Invalid quality submission time" ;;
      date)
        copy=$(jq -c '[.id += 1 | .submitted_at = "2026-02-30T00:00:00Z"]' <<<"$OBSERVED_REVIEW") || return 1
        expected="Invalid quality submission date" ;;
    esac
    for order in forward reverse; do
      if [[ "$order" == forward ]]; then
        printf '%s\n' "$copy" "$bound"
      else
        printf '%s\n' "$bound" "$copy"
      fi > "$MOCK_DIR/jbaruch__affected.reviews.7.json" || return 1
      if ! out=$(REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN" 2>"$MOCK_DIR/err"); then
        bad "quality sweep aborted on $mutation"; teardown; return
      fi
      err=$(cat "$MOCK_DIR/err") || return 1
      if ! jq -e 'map(.repo) == ["healthy"]' <<<"$out" >/dev/null ||
          [[ "$err" != *"unresolved for jbaruch/affected#7"* || "$err" != *"$expected"* ]]; then
        bad "$mutation: expected healthy work and a field-specific warning for affected PR"; teardown; return
      fi
      out=$(REVIEW_LANE=policy run_main) || return 1
      jq -e 'map(.repo) == ["healthy","affected"]' <<<"$out" >/dev/null || { bad "$mutation: missing policy work"; teardown; return; }
    done
  done
  ok "malformed quality ordering isolates one PR in a two-repository sweep"; teardown
}

t_missing_quality_dependency_stops_discovery() {
  setup; MOCK_REPOS=(jbaruch/healthy)
  writef "$MOCK_DIR/jbaruch__healthy.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/healthy"}}}]
JSON
  local tool tool_path out
  for tool in jq cat; do
    tool_path=$(command -v "$tool") || return 1
    ln -s "$tool_path" "$MOCK_DIR/$tool" || return 1
  done
  if out=$(PATH="$MOCK_DIR" REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN" 2>&1) ||
    [[ "$out" != *"Python 3 is required"* || -e "$MOCK_DIR/requests.log" ]]; then
    bad "missing interpreter must fail before GitHub discovery ($out)"; teardown; return
  fi
  if out=$(QUALITY_SELECTOR="$MOCK_DIR/absent.py" REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN" 2>&1) ||
    [[ "$out" != *"restore the complete coding-policy checkout"* || -e "$MOCK_DIR/requests.log" ]]; then
    bad "missing selector must fail before GitHub discovery ($out)"; teardown; return
  fi
  writef "$MOCK_DIR/invalid-output.py" <<'PYTHON'
print("null")
PYTHON
  writef "$MOCK_DIR/jbaruch__healthy.reviews.7.json" <<< '[{"id":7,"user":null}]'
  if ! out=$(QUALITY_SELECTOR="$MOCK_DIR/invalid-output.py" REVIEW_LANE=quality run_main "$CLAUDE_REVIEW_LOGIN" 2>"$MOCK_DIR/err") ||
      [[ "$out" != '[]' ]] || ! grep -q 'reinstall the matching' "$MOCK_DIR/err" ||
      ! grep -q 'unresolved for jbaruch/healthy#7' "$MOCK_DIR/err"; then
    bad "invalid selector output must hold the observed PR with reinstall guidance"; teardown; return
  fi
  out=$(PATH="$MOCK_DIR" REVIEW_LANE=policy run_main) || return 1
  jq -e 'map(.repo) == ["healthy"]' <<<"$out" >/dev/null || { bad "policy discovery should run without Python"; teardown; return; }
  ok "unavailable quality dependency fails setup without interrupting policy discovery"; teardown
}

t_dismissed_review_reenters_worklist() {
  setup; MOCK_REPOS=(jbaruch/repo-a)
  writef "$MOCK_DIR/jbaruch__repo-a.pulls.json" <<'JSON'
[{"number":7,"base":{"ref":"main"},"head":{"sha":"aaa111","repo":{"full_name":"jbaruch/repo-a"}}}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"id":40,"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"COMMENTED","submitted_at":"2026-01-01T00:00:00Z"},
 {"id":41,"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"DISMISSED","submitted_at":"2026-01-02T00:00:00Z","body":"blocking findings"}]
JSON
  writef "$MOCK_DIR/jbaruch__repo-a.timeline.7.json" <<'JSON'
[{"event":"review_dismissed","dismissed_review":{"review_id":41,"dismissal_message":"JUDGE-RULED: 0123456789abcdef covers 1 blocking findings at aaa111; tracked in #9"}}]
JSON
  local out
  out=$(run_main)
  [[ "$(jq length <<<"$out")" == 1 ]] || { bad "historical dismissal must permit fresh policy review"; teardown; return; }
  out=$(REVIEW_LANE=quality run_main)
  [[ "$(jq length <<<"$out")" == 1 ]] || { bad "dismissed policy review cannot suppress quality review"; teardown; return; }
  writef "$MOCK_DIR/jbaruch__repo-a.timeline.7.json" <<'JSON'
[{"event":"review_dismissed","dismissed_review":{"review_id":41,"dismissal_message":null}}]
JSON
  out=$(run_main)
  [[ "$(jq length <<<"$out")" == 1 ]] || { bad "a stale-dismissed current-head review must re-enter the worklist, got $out"; teardown; return; }
  writef "$MOCK_DIR/jbaruch__repo-a.reviews.7.json" <<'JSON'
[{"id":41,"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"DISMISSED","submitted_at":"2026-01-02T00:00:00Z"},
 {"id":42,"user":{"login":"app[bot]"},"commit_id":"aaa111","state":"APPROVED","submitted_at":"2026-01-03T00:00:00Z"}]
JSON
  out=$(run_main)
  [[ "$(jq length <<<"$out")" == 0 ]] || { bad "fresh native approval must deduplicate"; teardown; return; }
  ok "dismissed policy review re-enters the worklist until a fresh approval"; teardown
}

t_retired_repositories_are_never_queried() {
  local lane out
  for lane in policy quality; do
    setup
    MOCK_REPOS=(jbaruch/archived jbaruch/disabled jbaruch/active)
    MOCK_ARCHIVED_REPOS='["jbaruch/archived"]'
    MOCK_DISABLED_REPOS='["jbaruch/disabled"]'
    writef "$MOCK_DIR/jbaruch__active.pulls.json" <<'JSON'
[{"number":9,"base":{"ref":"main"},"head":{"sha":"abc123","repo":{"full_name":"jbaruch/active"}}}]
JSON
    out=$(REVIEW_LANE="$lane" run_main)
    if [[ "$(jq -r 'map(.repo) | join(",")' <<< "$out")" != active ]]; then
      bad "${lane}: active unreviewed repository must remain selected ($out)"; teardown; return
    fi
    if grep -Eq '/repos/jbaruch/(archived|disabled)/' "$MOCK_DIR/requests.log"; then
      bad "${lane}: retired repositories received a marker, PR or review query"; teardown; return
    else
      local rc=$?
      if [[ "$rc" != 1 ]]; then bad "${lane}: cannot inspect API request trace"; teardown; return; fi
    fi
    ok "${lane} excludes archived and disabled repositories before querying them"
    teardown
  done
}

echo "== fleet-poll.sh tests =="
t_retired_repositories_are_never_queried
t_dismissed_review_reenters_worklist
t_lanes_deduplicate_separately
t_missing_quality_dependency_stops_discovery || { echo "dependency fixture failed" >&2; exit 1; }
t_bad_quality_history_isolated_to_one_pr || { echo "quality fixture failed" >&2; exit 1; }
t_needs_review
t_already_reviewed
t_head_advanced
t_fork_skipped
t_unmarked_repo_skipped
t_other_reviewer_ignored
t_multi_repo
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
