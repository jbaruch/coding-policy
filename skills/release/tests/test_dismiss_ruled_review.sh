#!/usr/bin/env bash
# Outcome-based tests for dismiss-ruled-review.sh.
#
# The policy-review body is GOLDEN: each fixture runs the real
# .github/codex-review/post-review.sh against a PATH-stubbed gh that captures
# the posted payload, so a change to post-review.sh's finding format breaks
# these tests instead of silently breaking the parser. dismiss-ruled-review.sh
# is sourced (its main() guard prevents auto-run) and `gh` is overridden with a
# shell function serving fixtures and logging, in order, every follow-up
# comment POST and dismissal PUT. No network.
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
EVENTS="${TMPDIR_TEST}/events"            # "comment" / "dismiss <message>", in call order
COMMENT_BODY="${TMPDIR_TEST}/comment.md"  # last posted follow-up comment body
COMPARE_LOG="${TMPDIR_TEST}/compares"
RULING="${TMPDIR_TEST}/ruling"
ISSUE=12

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

assert_unmet() { # <substring> <label>
  jq -r '.unmet[]' <<<"$OUT" | grep -qF -- "$1" && return 0
  echo "    FAIL: unmet names $2 (got $(jq -c .unmet <<<"$OUT"))" >&2
  return 1
}

run() {
  local name="$1"; shift
  : > "$EVENTS"; : > "$COMPARE_LOG"; rm -f "$COMMENT_BODY"
  MOCK_CHECKS='[{"name":"tests","bucket":"pass"}]'
  MOCK_CHECKS_RC=0
  MOCK_COMPARE='{"status":"ahead","files":[]}'
  MOCK_ISSUE_COMMENTS='[]'
  MOCK_COMMENT_RC=0
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

# A complete schema_version 1 ruling. Args: <head> <finding-line>...
write_ruling() {
  local head="$1"; shift
  {
    echo "RULING: weighed"
    echo "schema_version: 1"
    echo "HEAD: ${head}"
    echo "ANSWER: decline the error-handling one, the harness sets it; b.md is presentation only"
    printf '%s\n' "$@"
    echo "ACTION: none"
    echo "UNVERIFIED: none"
  } > "$RULING"
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
      local method="GET" path="" message="" body_file="" saw_paginate=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -X) method="$2"; shift 2 ;;
          --paginate) saw_paginate=1; shift ;;
          -f) [[ "$2" == message=* ]] && message="${2#message=}"; shift 2 ;;
          -F) [[ "$2" == body=@* ]] && body_file="${2#body=@}"; method="POST"; shift 2 ;;
          repos/*) path="$1"; shift ;;
          *) shift ;;
        esac
      done
      if [[ "$method" == "PUT" && "$path" == */reviews/8/dismissals ]]; then
        printf 'dismiss %s\n' "$message" >> "$EVENTS"; echo '{}'; return 0
      fi
      if [[ "$method" == "POST" && "$path" == "repos/owner/repo/issues/${ISSUE}/comments" ]]; then
        [[ "$MOCK_COMMENT_RC" -eq 0 ]] || { echo "mock gh: comment POST failed" >&2; return "$MOCK_COMMENT_RC"; }
        cp "$body_file" "$COMMENT_BODY"; echo "comment" >> "$EVENTS"; echo '{}'; return 0
      fi
      case "$path" in
        */compare/*) echo "$path" >> "$COMPARE_LOG"; echo "$MOCK_COMPARE" ;;
        */issues/"${ISSUE}"/comments*)
          [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: comments fetch missing --paginate" >&2; return 99; }
          echo "$MOCK_ISSUE_COMMENTS" ;;
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
invoke_ruled() { invoke --ruling "$RULING" --followup-issue "$ISSUE"; }
dismissals() { grep -c '^dismiss ' "$EVENTS"; }
comments() { grep -c '^comment$' "$EVENTS"; }
digest_of_ruling() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:16])' "$RULING"; }

DECLINE_ONE="FINDING: policy skills/x/run.sh:3 error-handling — decline — rule text misread; the harness sets it"
DECLINE_TWO="FINDING: policy rules/b.md:7 context-writing-style — decline — presentation only"
DEFER_TWO="FINDING: policy rules/b.md:7 context-writing-style — defer — reword the bullet"

t_all_covered_posts_followup_then_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DEFER_TWO"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  local digest; digest=$(digest_of_ruling)
  assert_eq "comment then dismissal" \
    "comment|dismiss JUDGE-RULED: ${digest} covers 2 blocking findings at ${HEAD_SHA}; tracked in #${ISSUE}" \
    "$(paste -sd'|' "$EVENTS")" || return 1
  grep -qF "judge ruling ${digest}" "$COMMENT_BODY" || { echo "    FAIL: comment cites the digest" >&2; return 1; }
  grep -qF "skills/x/run.sh:3\` **error-handling** — declined, won't-fix: rule text misread" "$COMMENT_BODY" \
    || { echo "    FAIL: decline entered as won't-fix" >&2; return 1; }
  grep -qF "rules/b.md:7\` **context-writing-style** — deferred: reword the bullet" "$COMMENT_BODY" \
    || { echo "    FAIL: defer entered" >&2; return 1; }
  assert_eq "no compare at head" "0" "$(wc -l < "$COMPARE_LOG" | tr -d ' ')"
}

t_existing_followup_comment_is_reused() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled   # first run posts the generated entry; capture it
  assert_eq "first run exit" "0" "$RC" || return 1
  MOCK_ISSUE_COMMENTS=$(jq -cn --rawfile b "$COMMENT_BODY" '[{"id":1,"body":("\n" + $b + "\n")}]')
  : > "$EVENTS"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "no second comment" "0" "$(comments)" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_partial_comment_citing_digest_is_not_reused() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_ISSUE_COMMENTS=$(jq -cn --arg b "judge ruling $(digest_of_ruling) — nothing listed" '[{"id":1,"body":$b}]')
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "comment then dismissal" "comment" "$(head -1 "$EVENTS")" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_failed_followup_post_dismisses_nothing() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_COMMENT_RC=1
  invoke_ruled
  assert_eq "exit" "2" "$RC" || return 1
  assert_eq "stdout empty" "" "$OUT" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_one_uncovered_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_fix_ruling_leaves_finding_uncovered() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "FINDING: policy rules/b.md:7 context-writing-style — fix"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_different_line_does_not_cover() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "FINDING: policy rules/b.md:9 context-writing-style — decline — other line"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_unmet "naming no blocking finding" "the unmatched FINDING line" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_same_path_different_rule_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "FINDING: policy rules/b.md:7 review-severity — decline — other rule"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_duplicate_finding_line_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO" "$DEFER_TWO"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "duplicate FINDING" "the duplicate" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_unmatched_finding_line_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO" "FINDING: policy gone.sh:1 error-handling — decline — fixed already"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "gone.sh:1" "the unmatched FINDING line" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_floor_rule_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_FLOOR"
  write_ruling "$HEAD_SHA" "FINDING: policy a.sh:1 no-secrets — decline — test token"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "floor rule" "the floor" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_failing_check_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_CHECKS='[{"name":"tests","bucket":"fail"},{"name":"lint","bucket":"pending"}]'
  MOCK_CHECKS_RC=8
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "failing check" "the failing check" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_pending_checks_do_not_refuse() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_CHECKS='[{"name":"tests","bucket":"pending"}]'
  MOCK_CHECKS_RC=8
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_not_on_head_is_noop() {
  set_review CHANGES_REQUESTED "$OLD_SHA" "$BODY_TWO"
  write_ruling "$OLD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_not_changes_requested_is_noop() {
  set_review COMMENTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_idempotent_rerun_is_noop() {
  set_review DISMISSED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_unparseable_body_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" $'Summary\n\n## Blocking findings (gate the merge)\n- free text the parser cannot read'
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "Blocking findings" "the unparseable section" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_carry_over_unchanged_path_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$OLD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"README.md"}]}'
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "compare base...head" "repos/owner/repo/compare/${OLD_SHA}...${HEAD_SHA}" "$(cat "$COMPARE_LOG")" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_carry_over_changed_path_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$OLD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"rules/b.md"}]}'
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_carry_over_diverged_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$OLD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_COMPARE='{"status":"diverged","files":[]}'
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "diverged" "the diverged compare" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_missing_answer_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  grep -v '^ANSWER:' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit without ANSWER" "1" "$RC" || return 1
  assert_unmet "ANSWER:" "the missing answer" || return 1
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed 's/^ANSWER:.*/ANSWER:   /' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit with empty ANSWER" "1" "$RC" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_schema_missing_or_other_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  grep -v '^schema_version:' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit without schema_version" "1" "$RC" || return 1
  assert_unmet "schema_version: 1" "the schema" || return 1
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed 's/^schema_version: 1$/schema_version: 2/' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit with schema_version 2" "1" "$RC" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_malformed_ruling_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed '1s/.*/RULING: insufficient — need the call path/' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "insufficient ruling exit" "1" "$RC" || return 1
  assert_unmet "RULING: weighed" "the ruling line" || return 1
  write_ruling "$HEAD_SHA" "FINDING: policy skills/x/run.sh:3 error-handling — decline" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "decline without reason exit" "1" "$RC" || return 1
  assert_unmet "unparseable FINDING" "the FINDING line" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_list_mode_emits_findings() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  invoke
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "findings" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "blocking only" "skills/x/run.sh:3:error-handling,rules/b.md:7:context-writing-style" \
    "$(jq -r '.findings | map("\(.path):\(.line):\(.rule)") | join(",")' <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_usage_errors_exit_2() {
  RC=0; ( main owner repo ) >/dev/null 2>&1 || RC=$?
  assert_eq "missing pr" "2" "$RC" || return 1
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke --ruling "$RULING"
  assert_eq "ruling without --followup-issue" "2" "$RC" || return 1
  invoke --ruling "${TMPDIR_TEST}/does-not-exist" --followup-issue "$ISSUE"
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
run "covered: follow-up comment posted, then dismissal" t_all_covered_posts_followup_then_dismisses
run "an existing follow-up comment is reused"       t_existing_followup_comment_is_reused
run "a partial comment citing the digest is not reused" t_partial_comment_citing_digest_is_not_reused
run "a failed follow-up post dismisses nothing"     t_failed_followup_post_dismisses_nothing
run "one uncovered finding refuses"                 t_one_uncovered_refuses
run "a fix ruling leaves the finding uncovered"     t_fix_ruling_leaves_finding_uncovered
run "a different line does not cover"               t_different_line_does_not_cover
run "same path, different rule refuses"             t_same_path_different_rule_refuses
run "a duplicate FINDING line refuses"              t_duplicate_finding_line_refuses
run "an unmatched FINDING line refuses"             t_unmatched_finding_line_refuses
run "a floor rule refuses"                          t_floor_rule_refuses
run "a failing check refuses"                       t_failing_check_refuses
run "pending checks do not refuse"                  t_pending_checks_do_not_refuse
run "a review not on the head is a noop"            t_not_on_head_is_noop
run "a non-CHANGES_REQUESTED review is a noop"      t_not_changes_requested_is_noop
run "an idempotent re-run is a noop"                t_idempotent_rerun_is_noop
run "an unparseable body refuses"                   t_unparseable_body_refuses
run "carry-over with the path unchanged dismisses"  t_carry_over_unchanged_path_dismisses
run "carry-over with the path changed refuses"      t_carry_over_changed_path_refuses
run "carry-over across a diverged compare refuses"  t_carry_over_diverged_refuses
run "a missing or empty ANSWER refuses"             t_missing_answer_refuses
run "a missing or other schema_version refuses"             t_schema_missing_or_other_refuses
run "a malformed ruling refuses"                    t_malformed_ruling_refuses
run "list mode emits the blocking findings"         t_list_mode_emits_findings
run "usage errors exit 2"                           t_usage_errors_exit_2
run "the marker is pinned across three scripts"     t_marker_pinned_across_scripts

echo
echo "passed: ${PASS_COUNT}, failed: ${FAIL_COUNT}"
[[ $FAIL_COUNT -eq 0 ]]
