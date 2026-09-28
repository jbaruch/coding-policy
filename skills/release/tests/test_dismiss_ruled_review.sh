#!/usr/bin/env bash
# Outcome-based tests for dismiss-ruled-review.sh.
#
# The policy-review body is GOLDEN: each fixture runs the real
# .github/codex-review/post-review.sh against a PATH-stubbed gh that captures
# the posted payload, so a change to post-review.sh's finding format breaks
# these tests instead of silently breaking the parser. dismiss-ruled-review.sh
# is sourced (its main() guard prevents auto-run) and `gh` is overridden with a
# shell function serving fixtures and logging every dismissal PUT and compare
# call. No network.
#
# Run: bash skills/release/tests/test_dismiss_ruled_review.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch)
set -uo pipefail

RELEASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${RELEASE_DIR}/../.." && pwd)"
SCRIPT="${RELEASE_DIR}/dismiss-ruled-review.sh"
POSTER="${REPO_ROOT}/.github/codex-review/post-review.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: dismiss-ruled-review.sh not readable at $SCRIPT" >&2; exit 2; }
[[ -f "$POSTER" && -r "$POSTER" ]] || { echo "fatal: post-review.sh not readable at $POSTER" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq is required to run these tests" >&2; exit 2; }

# shellcheck disable=SC1090  # ShellCheck cannot resolve the dynamically constructed source path.
source "$SCRIPT"
set +e

TMPDIR_TEST=$(mktemp -d -t dismiss-ruled-test.XXXXXX) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
cleanup_tmp() {
  if ! rm -rf "$TMPDIR_TEST"; then
    echo "warning: could not remove temp dir ${TMPDIR_TEST} — remove it by hand" >&2
  fi
  return 0
}
trap cleanup_tmp EXIT

PASS_COUNT=0
FAIL_COUNT=0
HEAD_SHA="1111111111111111111111111111111111111111"
OLD_SHA="2222222222222222222222222222222222222222"
DISMISS_LOG="${TMPDIR_TEST}/dismissals"
COMPARE_LOG="${TMPDIR_TEST}/compares"

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  : > "$DISMISS_LOG"; : > "$COMPARE_LOG"
  MOCK_CHECKS='[{"name":"tests","bucket":"pass"}]'
  MOCK_CHECKS_RC=0
  MOCK_COMPARE='{"status":"ahead","files":[]}'
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Golden body: post the Codex result JSON through the real post-review.sh with
# a gh stub on PATH that captures the review payload, then read its body.
STUB_DIR="${TMPDIR_TEST}/stub"
mkdir -p "$STUB_DIR" || { echo "fatal: cannot create $STUB_DIR" >&2; exit 2; }
cat > "${STUB_DIR}/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cat > "$GH_CAPTURE"
SH
chmod +x "${STUB_DIR}/gh"

golden_body() { # <codex-result-json>
  local result="${TMPDIR_TEST}/final.json" capture="${TMPDIR_TEST}/payload.json"
  printf '%s' "$1" > "$result"
  if ! GH_CAPTURE="$capture" PATH="${STUB_DIR}:${PATH}" bash "$POSTER" owner repo 9 "$result" >/dev/null; then
    echo "fatal: post-review.sh failed to build the golden body" >&2
    return 1
  fi
  jq -r '.body' "$capture"
}

BODY_TWO=$(golden_body '{"summary":"Policy loaded: 26 rule files. Two violations.","findings":[
  {"path":"skills/x/run.sh","line":3,"rule":"error-handling","severity":"blocking","message":"missing set -euo pipefail; add it"},
  {"path":"rules/b.md","line":7,"rule":"context-writing-style","severity":"blocking","message":"colon attaches a rationale"},
  {"path":"rules/c.md","line":2,"rule":"context-writing-style","severity":"advisory","message":"prefer a synonym"}]}') \
  || exit 2
BODY_FLOOR=$(golden_body '{"summary":"Policy loaded: 26 rule files. Secret.","findings":[
  {"path":"a.sh","line":1,"rule":"no-secrets","severity":"blocking","message":"hardcoded token"}]}') \
  || exit 2

# One policy review fixture. Args: <state> <commit> <body>
set_review() {
  MOCK_REVIEWS=$(jq -cn --arg state "$1" --arg commit "$2" --arg body "$3" \
    '[{"id":7,"user":{"login":"github-actions[bot]"},"state":"COMMENTED","commit_id":"0000","submitted_at":"2026-01-01T00:00:00Z","body":"older"},
      {"id":8,"user":{"login":"github-actions[bot]"},"state":$state,"commit_id":$commit,"submitted_at":"2026-01-02T00:00:00Z","body":$body}]')
}

write_ruling() { # <path> <head> <finding-line>...
  local path="$1" head="$2"; shift 2
  {
    echo "RULING: weighed"
    echo "HEAD: ${head}"
    printf '%s\n' "$@"
    echo "ACTION: none"
    echo "UNVERIFIED: none"
  } > "$path"
}

gh() {
  case "$1" in
    pr)
      case "$2" in
        view)   echo "$HEAD_SHA" ;;
        checks) echo "$MOCK_CHECKS"; return "$MOCK_CHECKS_RC" ;;
        *) echo "mock gh pr: unsupported: $*" >&2; return 2 ;;
      esac
      ;;
    api)
      shift
      local method="GET" path="" message="" saw_paginate=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -X) method="$2"; shift 2 ;;
          --paginate) saw_paginate=1; shift ;;
          -f) [[ "$2" == message=* ]] && message="${2#message=}"; shift 2 ;;
          repos/*) path="$1"; shift ;;
          *) shift ;;
        esac
      done
      if [[ "$method" == "PUT" && "$path" == */reviews/8/dismissals ]]; then
        printf '%s\n' "$message" >> "$DISMISS_LOG"; echo '{}'; return 0
      fi
      case "$path" in
        */compare/*) echo "$path" >> "$COMPARE_LOG"; echo "$MOCK_COMPARE" ;;
        *reviews*)
          [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: reviews fetch missing --paginate" >&2; return 99; }
          echo "$MOCK_REVIEWS" ;;
        *) echo "mock gh api: unsupported: $method $path" >&2; return 2 ;;
      esac
      ;;
    *) echo "mock gh: unsupported: $*" >&2; return 2 ;;
  esac
}

# Run main in a subshell (it exits); capture stdout and rc.
OUT=""; RC=0
invoke() { RC=0; OUT=$( (main owner repo 5 "$@") 2>"${TMPDIR_TEST}/stderr") || RC=$?; }
dismissals() { wc -l < "$DISMISS_LOG" | tr -d ' '; }

COVER_BOTH=("FINDING: policy skills/x/run.sh:3 error-handling — decline — rule text misread; the harness sets it"
            "FINDING: policy rules/b.md:7 context-writing-style — decline — presentation only")

t_all_covered_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  local ruling="${TMPDIR_TEST}/ruling"
  write_ruling "$ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  invoke --ruling "$ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "findings parsed" "2" "$(jq '.findings | length' <<<"$OUT")" || return 1
  assert_eq "one PUT" "1" "$(dismissals)" || return 1
  local digest; digest=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:16])' "$ruling")
  assert_eq "message" "JUDGE-RULED: ${digest} covers 2 blocking findings at ${HEAD_SHA}" "$(cat "$DISMISS_LOG")" || return 1
  assert_eq "no compare at head" "0" "$(wc -l < "$COMPARE_LOG" | tr -d ' ')"
}

t_one_uncovered_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[0]}"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_fix_line_blocks_coverage() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}" \
    "FINDING: policy rules/b.md:7 context-writing-style — fix"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_floor_rule_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_FLOOR"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "FINDING: policy a.sh:1 no-secrets — decline — test token"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "floor" || { echo "    FAIL: unmet names the floor" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_failing_check_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  MOCK_CHECKS='[{"name":"tests","bucket":"fail"},{"name":"lint","bucket":"pending"}]'
  MOCK_CHECKS_RC=8
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "failing check" || { echo "    FAIL: unmet names the failing check" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_pending_checks_do_not_refuse() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  MOCK_CHECKS='[{"name":"tests","bucket":"pending"}]'
  MOCK_CHECKS_RC=8
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "one PUT" "1" "$(dismissals)"
}

t_not_on_head_is_noop() {
  set_review CHANGES_REQUESTED "$OLD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$OLD_SHA" "${COVER_BOTH[@]}"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_not_changes_requested_is_noop() {
  set_review COMMENTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_idempotent_rerun_is_noop() {
  set_review DISMISSED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_unparseable_body_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" $'Summary\n\n## Blocking findings (gate the merge)\n- free text the parser cannot read'
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[@]}"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "Blocking findings" || { echo "    FAIL: unmet names the unparseable section" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_carry_over_unchanged_path_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$OLD_SHA" "${COVER_BOTH[@]}"
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"README.md"}]}'
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "compare base...head" "repos/owner/repo/compare/${OLD_SHA}...${HEAD_SHA}" "$(cat "$COMPARE_LOG")" || return 1
  assert_eq "one PUT" "1" "$(dismissals)"
}

t_carry_over_changed_path_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$OLD_SHA" "${COVER_BOTH[@]}"
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"rules/b.md"}]}'
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_carry_over_diverged_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$OLD_SHA" "${COVER_BOTH[@]}"
  MOCK_COMPARE='{"status":"diverged","files":[]}'
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "diverged" || { echo "    FAIL: unmet names the diverged compare" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_same_path_different_rule_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[0]}" \
    "FINDING: policy rules/b.md:7 review-severity — decline — other rule"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_defer_without_followup_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[0]}" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — reword the bullet"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q -- "--followup" || { echo "    FAIL: unmet names --followup" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_defer_with_followup_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "${COVER_BOTH[0]}" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — reword the bullet"
  invoke --ruling "${TMPDIR_TEST}/ruling" --followup "https://github.com/owner/repo/issues/12"
  assert_eq "exit" "0" "$RC" || return 1
  case "$(cat "$DISMISS_LOG")" in
    "JUDGE-RULED: "*"; deferred to https://github.com/owner/repo/issues/12") ;;
    *) echo "    FAIL: message names the follow-up: $(cat "$DISMISS_LOG")" >&2; return 1 ;;
  esac
}

t_malformed_ruling_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  printf 'RULING: insufficient — need the call path\nHEAD: %s\n%s\n' "$HEAD_SHA" "${COVER_BOTH[0]}" > "${TMPDIR_TEST}/ruling"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "insufficient ruling exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "RULING: weighed" || { echo "    FAIL: unmet names the ruling line" >&2; return 1; }
  write_ruling "${TMPDIR_TEST}/ruling" "$HEAD_SHA" "FINDING: policy skills/x/run.sh:3 error-handling — decline"
  invoke --ruling "${TMPDIR_TEST}/ruling"
  assert_eq "decline without reply exit" "1" "$RC" || return 1
  jq -r '.unmet[]' <<<"$OUT" | grep -q "unparseable FINDING" || { echo "    FAIL: unmet names the FINDING line" >&2; return 1; }
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_list_mode_emits_findings() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  invoke
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "findings" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "blocking only" "skills/x/run.sh:3:error-handling,rules/b.md:7:context-writing-style" \
    "$(jq -r '.findings | map("\(.path):\(.line):\(.rule)") | join(",")' <<<"$OUT")" || return 1
  assert_eq "no PUT" "0" "$(dismissals)"
}

t_usage_errors_exit_2() {
  RC=0; ( main owner repo ) >/dev/null 2>&1 || RC=$?
  assert_eq "missing pr" "2" "$RC" || return 1
  invoke --ruling "${TMPDIR_TEST}/does-not-exist"
  assert_eq "unreadable ruling" "2" "$RC" || return 1
  assert_eq "stdout empty" "" "$OUT"
}

t_marker_pinned_across_scripts() {
  local s marker
  for s in dismiss-ruled-review.sh poll-pr-reviews.sh dismiss-stale-reviews.sh; do
    marker=$(grep -E '^RULED_MARKER=' "${RELEASE_DIR}/${s}")
    assert_eq "marker in ${s}" 'RULED_MARKER="JUDGE-RULED:"' "$marker" || return 1
  done
}

echo "test_dismiss_ruled_review.sh"
run "all findings covered at head dismisses"      t_all_covered_dismisses
run "one uncovered finding refuses"               t_one_uncovered_refuses
run "a fix line for the same finding refuses"     t_fix_line_blocks_coverage
run "a floor rule refuses"                        t_floor_rule_refuses
run "a failing check refuses"                     t_failing_check_refuses
run "pending checks do not refuse"                t_pending_checks_do_not_refuse
run "a review not on the head is a noop"          t_not_on_head_is_noop
run "a non-CHANGES_REQUESTED review is a noop"    t_not_changes_requested_is_noop
run "an idempotent re-run is a noop"              t_idempotent_rerun_is_noop
run "an unparseable body refuses"                 t_unparseable_body_refuses
run "carry-over with the path unchanged dismisses" t_carry_over_unchanged_path_dismisses
run "carry-over with the path changed refuses"    t_carry_over_changed_path_refuses
run "carry-over across a diverged compare refuses" t_carry_over_diverged_refuses
run "same path, different rule refuses"           t_same_path_different_rule_refuses
run "defer without --followup refuses"            t_defer_without_followup_refuses
run "defer with --followup dismisses"             t_defer_with_followup_dismisses
run "a malformed ruling refuses"                  t_malformed_ruling_refuses
run "list mode emits the blocking findings"       t_list_mode_emits_findings
run "usage errors exit 2"                         t_usage_errors_exit_2
run "the marker is pinned across three scripts"   t_marker_pinned_across_scripts

echo
echo "passed: ${PASS_COUNT}, failed: ${FAIL_COUNT}"
[[ $FAIL_COUNT -eq 0 ]]
