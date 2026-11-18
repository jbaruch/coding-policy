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

RELEASE_DIR="$(cd "$(dirname "$0")/../../../skills/release" && pwd)"
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
  : > "$EVENTS" || { echo "fatal: cannot reset ${EVENTS} — check ${TMPDIR_TEST} is writable, then rerun" >&2; exit 2; }
  : > "$COMPARE_LOG" || { echo "fatal: cannot reset ${COMPARE_LOG} — check ${TMPDIR_TEST} is writable, then rerun" >&2; exit 2; }
  rm -f "$COMMENT_BODY" || { echo "fatal: cannot remove ${COMMENT_BODY} — check ${TMPDIR_TEST} is writable, then rerun" >&2; exit 2; }
  MOCK_CHECKS='[{"name":"tests","bucket":"pass"}]'
  MOCK_CHECKS_RC=0
  MOCK_COMPARE='{"status":"ahead","files":[]}'
  MOCK_ISSUE_COMMENTS='[]'
  MOCK_COMMENT_RC=0
  MOCK_COMMENTS_RC=0
  MOCK_DISMISS_RC=0
  RECORD_NUMBER="$ISSUE"
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name" >&2
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Golden body: post the Codex result JSON through the real post-review.sh with
# a gh stub on PATH that captures the review payload, then read its body. The
# stub answers the poster's already-reviewed lookup with no reviews.
STUB_DIR="${TMPDIR_TEST}/stub"
mkdir -p "$STUB_DIR" || { echo "fatal: cannot create $STUB_DIR" >&2; exit 2; }
cat > "${STUB_DIR}/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" != *"--method POST"* ]]; then echo '[]'; exit 0; fi
cat > "$GH_CAPTURE"
SH
chmod +x "${STUB_DIR}/gh"

golden_body() { # <codex-result-json>
  local result="${TMPDIR_TEST}/final.json" capture="${TMPDIR_TEST}/payload.json"
  printf '%s' "$1" > "$result"
  if ! GH_CAPTURE="$capture" REVIEWER_LOGIN="github-actions[bot]" PATH="${STUB_DIR}:${PATH}" bash "$POSTER" owner repo 9 "$result" "$HEAD_SHA" >/dev/null; then
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
BODY_DUPLICATE=$(golden_body '{"summary":"Policy loaded: 26 rule files. Same anchor.","findings":[
  {"path":"rules/b.md","line":7,"rule":"context-writing-style","severity":"blocking","message":"colon attaches a rationale"},
  {"path":"rules/b.md","line":7,"rule":"context-writing-style","severity":"blocking","message":"semicolon attaches another"}]}') \
  || exit 2

# One policy review fixture. Args: <state> <commit> <body>
set_review() {
  MOCK_REVIEWS=$(jq -cn --arg state "$1" --arg commit "$2" --arg body "$3" \
    '[{"id":7,"user":{"login":"github-actions[bot]"},"state":"COMMENTED","commit_id":"0000","submitted_at":"2026-01-01T00:00:00Z","body":"older"},
      {"id":8,"user":{"login":"github-actions[bot]"},"state":$state,"commit_id":$commit,"submitted_at":"2026-01-02T00:00:00Z","body":$body}]')
}

# A complete schema_version 4 operator ruling. Args: <head> <finding-line>...
write_ruling() {
  local head="$1"; shift
  {
    echo "RULING: weighed"
    echo "schema_version: 4"
    echo "AUTHORITY: operator"
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
        [[ "$MOCK_DISMISS_RC" -eq 0 ]] || { echo "mock gh: dismissal PUT failed" >&2; return "$MOCK_DISMISS_RC"; }
        printf 'dismiss %s\n' "$message" >> "$EVENTS"; echo '{}'; return 0
      fi
      if [[ "$method" == "POST" && "$path" == "repos/owner/repo/issues/${RECORD_NUMBER}/comments" ]]; then
        [[ "$MOCK_COMMENT_RC" -eq 0 ]] || { echo "mock gh: comment POST failed" >&2; return "$MOCK_COMMENT_RC"; }
        cp "$body_file" "$COMMENT_BODY"; echo "comment" >> "$EVENTS"; echo '{}'; return 0
      fi
      case "$path" in
        */compare/*) echo "$path" >> "$COMPARE_LOG"; echo "$MOCK_COMPARE" ;;
        */issues/"${RECORD_NUMBER}"/comments*)
          [[ "$MOCK_COMMENTS_RC" -eq 0 ]] || { echo "mock gh: comments GET failed" >&2; return "$MOCK_COMMENTS_RC"; }
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

# A judge's collected report, written as native factory collection writes it
# (factory.py save_report): its artifacts verbatim, joined by \n, plus a final
# \n. It is the ruling file itself. Args: <file> <artifact>...
collect_report() {
  local file="$1"; shift
  python3 -c 'import sys; open(sys.argv[1], "w", encoding="utf-8").write("\n".join(sys.argv[2:]) + "\n")' "$file" "$@" \
    || { echo "fatal: cannot write ${file}" >&2; exit 2; }
}

# The fixed judge report: a decline and a cost-grounded defer at HEAD_SHA, and
# an unknown judge line. Its sha256 was computed once outside the suite with
# `shasum -a 256`.
JUDGE_REPORT="${TMPDIR_TEST}/coding-policy-1.judge1.claude.report.txt"
collect_report "$JUDGE_REPORT" "RULING: weighed" "schema_version: 4" "AUTHORITY: judge" "HEAD: ${HEAD_SHA}" \
  "FINDING: policy skills/x/run.sh:3 error-handling — decline — wrong: the harness sets the flag" \
  "FINDING: policy rules/b.md:7 context-writing-style — defer — out-of-scope #12: pre-existing on main; cost: a reword touches every rule file for a presentation gain" \
  "ACTION: none" "UNVERIFIED: none" "operator_decision: none"
JUDGE_REPORT_SHA256="54bb4d28b70130017f26094eb6ab3a928baff6dca2931679afae0730945c7aa1"

# A judge report a test writes, in REPORT, with its sha256 in REPORT_SHA256.
# Args: <head> <finding-line>...
REPORT="${TMPDIR_TEST}/judge.report.txt"
REPORT_SHA256=""
write_judge_report() {
  local head="$1"; shift
  collect_report "$REPORT" "RULING: weighed" "schema_version: 4" "AUTHORITY: judge" "HEAD: ${head}" "$@" "ACTION: none"
  REPORT_SHA256=$(sha256_of "$REPORT") \
    || { echo "fatal: cannot hash ${REPORT}" >&2; exit 2; }
}

# Run main in a subshell (it exits); capture stdout and rc.
OUT=""; RC=0
invoke() { RC=0; OUT=$( (main owner repo 5 "$@") 2>"${TMPDIR_TEST}/stderr") || RC=$?; }
invoke_ruled() { invoke --ruling "$RULING" --followup-issue "$ISSUE"; }
dismissals() { grep -c '^dismiss ' "$EVENTS"; }
comments() { grep -c '^comment$' "$EVENTS"; }
sha256_of() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
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
    "comment|dismiss JUDGE-RULED: ${digest} covers 2 blocking findings at ${HEAD_SHA}; recorded in #${ISSUE}" \
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

t_operator_records_in_pr_without_issue() {
  RECORD_NUMBER=5
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DEFER_TWO"
  invoke --ruling "$RULING"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "no follow-up issue" "null" "$(jq -r .followup_issue <<<"$OUT")" || return 1
  assert_eq "comment then dismissal" "comment|dismiss" "$(cut -d' ' -f1 "$EVENTS" | paste -sd'|' -)" || return 1
  assert_eq "PR cited" "recorded in #5" "$(jq -r '.message | split("; ")[1]' <<<"$OUT")" || return 1
  grep -qF 'deferred: reword the bullet' "$COMMENT_BODY" || return 1
  grep -qF "declined, won't-fix: rule text misread" "$COMMENT_BODY"
}

t_pr_record_errors_dismiss_nothing() {
  RECORD_NUMBER=5
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_COMMENTS_RC=1
  invoke --ruling "$RULING"
  assert_eq "read error exit" "2" "$RC" || return 1
  assert_eq "read error stdout" "" "$OUT" || return 1
  assert_eq "read error: nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')" || return 1
  MOCK_COMMENTS_RC=0
  MOCK_COMMENT_RC=1
  invoke --ruling "$RULING"
  assert_eq "post error exit" "2" "$RC" || return 1
  assert_eq "post error stdout" "" "$OUT" || return 1
  assert_eq "post error: nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_pr_record_reused_after_dismissal_error() {
  RECORD_NUMBER=5
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  MOCK_DISMISS_RC=1
  invoke --ruling "$RULING"
  assert_eq "dismissal error exit" "2" "$RC" || return 1
  assert_eq "dismissal error stdout" "" "$OUT" || return 1
  assert_eq "record posted" "1" "$(comments)" || return 1
  MOCK_ISSUE_COMMENTS=$(jq -cn --rawfile b "$COMMENT_BODY" '[{"id":1,"body":$b}]')
  MOCK_DISMISS_RC=0
  : > "$EVENTS"
  invoke --ruling "$RULING"
  assert_eq "retry exit" "0" "$RC" || return 1
  assert_eq "no duplicate record" "0" "$(comments)" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
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

t_duplicate_review_finding_needs_matching_count() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_DUPLICATE"
  write_ruling "$HEAD_SHA" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_eq "one uncovered" "1" "$(jq '.uncovered | length' <<<"$OUT")" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_duplicate_review_findings_with_same_verdict_dismiss() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_DUPLICATE"
  write_ruling "$HEAD_SHA" "$DECLINE_TWO" \
    "FINDING: policy rules/b.md:7 context-writing-style — decline — second presentation finding"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "dismissed" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "two findings" "2" "$(jq '.findings | length' <<<"$OUT")" || return 1
  assert_eq "two follow-up entries" "2" "$(grep -c 'rules/b.md:7' "$COMMENT_BODY")" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_duplicate_review_findings_with_mixed_verdicts_refuse() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_DUPLICATE"
  write_ruling "$HEAD_SHA" "$DECLINE_TWO" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — track the second finding"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "different verdicts" "the ambiguous outcomes" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

t_rule_names_do_not_refuse_operator() {
  local rule body
  for rule in no-secrets ci-safety; do
    body=$(golden_body "{\"summary\":\"Harmless finding\",\"findings\":[{\"path\":\"a.sh\",\"line\":1,\"rule\":\"$rule\",\"severity\":\"blocking\",\"message\":\"incorrectly applied rule\"}]}") || return 1
    set_review CHANGES_REQUESTED "$HEAD_SHA" "$body"
    write_ruling "$HEAD_SHA" "FINDING: policy a.sh:1 $rule — decline — incorrectly applied rule"
    invoke_ruled
    assert_eq "$rule exit" "0" "$RC" || return 1
    assert_eq "$rule result" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  done
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
  assert_eq "no dismissal" "0" "$(dismissals)" || return 1
  # A new current-head ruling attests applicability after an unrelated edit
  # in the finding's file; the script never classifies semantic applicability.
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "current-head ruling exit" "0" "$RC" || return 1
  assert_eq "current-head ruling dismisses" "1" "$(dismissals)"
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
  assert_unmet "schema_version: 4" "the schema" || return 1
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed 's/^schema_version: 4$/schema_version: 3/' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit with schema_version 3" "1" "$RC" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

# Args: <invoke-fn> <writer-fn>. A ruling one schema above RULING_SCHEMA.
check_newer_schema_refused_unmodified() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  "$2" "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  local newer=$((RULING_SCHEMA + 1)) before after
  sed "s/^schema_version: ${RULING_SCHEMA}\$/schema_version: ${newer}/" "$RULING" > "${RULING}.tmp" \
    && mv "${RULING}.tmp" "$RULING"
  before=$(digest_of_ruling) || { echo "    FAIL: could not digest the ruling before the run" >&2; return 1; }
  [[ -n "$before" ]] || { echo "    FAIL: empty digest of the ruling before the run" >&2; return 1; }
  "$1"
  after=$(digest_of_ruling) || { echo "    FAIL: could not digest the ruling after the run" >&2; return 1; }
  [[ -n "$after" ]] || { echo "    FAIL: empty digest of the ruling after the run" >&2; return 1; }
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "schema ${newer}, newer than this script accepts (${RULING_SCHEMA}) — update the coding-policy plugin" "the newer schema" || return 1
  assert_eq "ruling bytes unchanged" "$before" "$after" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}
t_newer_schema_refuses_unmodified() { check_newer_schema_refused_unmodified invoke_ruled write_ruling; }

t_version_2_ruling_refuses_unmodified() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed 's/^schema_version: 4$/schema_version: 2/' "$RULING" > "${RULING}.tmp" \
    && mv "${RULING}.tmp" "$RULING"
  local before after
  before=$(digest_of_ruling) || { echo "    FAIL: could not digest the ruling before the run" >&2; return 1; }
  invoke_ruled
  after=$(digest_of_ruling) || { echo "    FAIL: could not digest the ruling after the run" >&2; return 1; }
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "rewrite an operator ruling in the current format" "the old schema" || return 1
  assert_eq "ruling bytes unchanged" "$before" "$after" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_non_utf8_ruling_refuses_without_traceback() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  printf 'RULING: weighed\nschema_version: 4\nAUTHORITY: operator\nANSWER: \xff\xfe\n' > "$RULING"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "FINDING" "the missing findings" || return 1
  if grep -q "Traceback" "${TMPDIR_TEST}/stderr"; then echo "    FAIL: traceback on stderr" >&2; return 1; fi
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
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
  write_ruling "${HEAD_SHA:0:12}" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "short HEAD sha exit" "1" "$RC" || return 1
  assert_unmet "HEAD: <40-hex sha>" "the HEAD line" || return 1
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
  invoke --ruling "$RULING" --followup-issue invalid
  assert_eq "invalid --followup-issue" "2" "$RC" || return 1
  invoke --ruling "$RULING" --followup-issue 0
  assert_eq "zero --followup-issue" "2" "$RC" || return 1
  invoke --ruling "${TMPDIR_TEST}/does-not-exist" --followup-issue "$ISSUE"
  assert_eq "unreadable ruling" "2" "$RC" || return 1
  invoke --ruling "$RULING" --ruling-sha256 "${JUDGE_REPORT_SHA256:0:16}" --followup-issue "$ISSUE"
  assert_eq "short --ruling-sha256" "2" "$RC" || return 1
  invoke --ruling-sha256 "$JUDGE_REPORT_SHA256"
  assert_eq "--ruling-sha256 without --ruling" "2" "$RC" || return 1
  assert_eq "stdout empty" "" "$OUT"
}

JUDGE_DECLINE_ONE="FINDING: policy skills/x/run.sh:3 error-handling — decline — wrong: the harness sets the flag"
JUDGE_DECLINE_TWO="FINDING: policy rules/b.md:7 context-writing-style — decline — wrong: the clause names no rationale"
JUDGE_DEFER_TWO="FINDING: policy rules/b.md:7 context-writing-style — defer — out-of-scope #${ISSUE}: pre-existing on main; cost: a reword touches every rule file"
JUDGE_FIX_TWO="FINDING: policy rules/b.md:7 context-writing-style — fix"

invoke_report() { invoke --ruling "$REPORT" --ruling-sha256 "$REPORT_SHA256" --followup-issue "$ISSUE"; }

t_judge_records_in_pr_without_issue_argument() {
  RECORD_NUMBER=5
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  invoke --ruling "$REPORT" --ruling-sha256 "$REPORT_SHA256"
  assert_eq "decline exit" "0" "$RC" || return 1
  assert_eq "no issue required for decline" "null" "$(jq -r .followup_issue <<<"$OUT")" || return 1
  grep -qF "sha256 ${REPORT_SHA256}" "$COMMENT_BODY" || return 1
  : > "$EVENTS"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DEFER_TWO"
  invoke --ruling "$REPORT" --ruling-sha256 "$REPORT_SHA256"
  assert_eq "defer exit" "0" "$RC" || return 1
  grep -qF "deferred: out-of-scope #${ISSUE}:" "$COMMENT_BODY" || return 1
  assert_eq "comment then dismissal" "comment|dismiss" "$(cut -d' ' -f1 "$EVENTS" | paste -sd'|' -)"
}

# Args: <label> <unmet substring>. Runs the REPORT ruling and expects refusal
# with nothing posted or dismissed.
refuses_report() {
  invoke_report
  assert_eq "$1 exit" "1" "$RC" || return 1
  assert_unmet "$2" "the $1" || return 1
  assert_eq "$1: nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_judge_report_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  invoke --ruling "$JUDGE_REPORT" --ruling-sha256 "$JUDGE_REPORT_SHA256" --followup-issue "$ISSUE"
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "result" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "report bytes as collected, unchanged" "$JUDGE_REPORT_SHA256" "$(sha256_of "$JUDGE_REPORT")" || return 1
  assert_eq "comment then dismissal" \
    "comment|dismiss JUDGE-RULED: ${JUDGE_REPORT_SHA256:0:16} covers 2 blocking findings at ${HEAD_SHA}; recorded in #${ISSUE}" \
    "$(paste -sd'|' "$EVENTS")" || return 1
  grep -qxF "Ruled by the judge's report ${JUDGE_REPORT} (sha256 ${JUDGE_REPORT_SHA256})." "$COMMENT_BODY" \
    || { echo "    FAIL: comment cites the report's absolute path and full sha256" >&2; return 1; }
  grep -qF "skills/x/run.sh:3\` **error-handling** — declined, won't-fix: wrong: the harness sets the flag" "$COMMENT_BODY" \
    || { echo "    FAIL: decline entered with its ground" >&2; return 1; }
  grep -qF "rules/b.md:7\` **context-writing-style** — deferred: out-of-scope #${ISSUE}: pre-existing on main; cost: a reword" "$COMMENT_BODY" \
    || { echo "    FAIL: defer entered with its scope and cost grounds" >&2; return 1; }
  assert_eq "no compare at the judge's head" "0" "$(wc -l < "$COMPARE_LOG" | tr -d ' ')"
}

t_judge_decline_dismisses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  invoke_report
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)" || return 1
  grep -qF "rules/b.md:7\` **context-writing-style** — declined, won't-fix: wrong: the clause names no rationale" "$COMMENT_BODY" \
    || { echo "    FAIL: decline entered with its ground" >&2; return 1; }
}

t_judge_digest_missing_or_mismatched_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  invoke --ruling "$REPORT" --followup-issue "$ISSUE"
  assert_eq "missing digest exit" "1" "$RC" || return 1
  assert_unmet "a judge ruling needs --ruling-sha256" "the missing digest" || return 1
  local other="0000000000000000000000000000000000000000000000000000000000000000"
  invoke --ruling "$REPORT" --ruling-sha256 "$other" --followup-issue "$ISSUE"
  assert_eq "mismatched digest exit" "1" "$RC" || return 1
  assert_unmet "has sha256 ${REPORT_SHA256}, not the given ${other}" "the mismatched digest" || return 1
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke --ruling "$RULING" --ruling-sha256 "$other" --followup-issue "$ISSUE"
  assert_eq "operator mismatched digest exit" "1" "$RC" || return 1
  assert_unmet "not the given ${other}" "the operator ruling's mismatched digest" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_judge_report_edited_after_collection_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DEFER_TWO"
  sed 's/^FINDING: policy rules\/b.md:7 .*/'"${JUDGE_DECLINE_TWO//\//\\/}"'/' "$REPORT" > "${REPORT}.tmp" && mv "${REPORT}.tmp" "$REPORT"
  refuses_report "edited report" "not the given ${REPORT_SHA256}"
}

t_judge_unsupported_schema_refuses_unmodified() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  local v before
  for v in 3 5; do
    collect_report "$REPORT" "RULING: weighed" "schema_version: ${v}" "AUTHORITY: judge" "HEAD: ${HEAD_SHA}" \
      "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
    REPORT_SHA256=$(sha256_of "$REPORT")
    before=$(sha256_of "$REPORT")
    refuses_report "schema ${v}" "schema" || return 1
    assert_eq "schema ${v}: report unchanged" "$before" "$(sha256_of "$REPORT")" || return 1
  done
  collect_report "$REPORT" "RULING: weighed" "schema_version: 3" "AUTHORITY: judge" "HEAD: ${HEAD_SHA}" \
    "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  REPORT_SHA256=$(sha256_of "$REPORT")
  refuses_report "older judge report" "obtain a new judge report"
}

t_judge_missing_head_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  collect_report "$REPORT" "RULING: weighed" "schema_version: 4" "AUTHORITY: judge" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  REPORT_SHA256=$(sha256_of "$REPORT")
  refuses_report "missing HEAD" "HEAD: <40-hex sha>" || return 1
  assert_eq "nothing compared" "0" "$(wc -l < "$COMPARE_LOG" | tr -d ' ')"
}

t_judge_incomplete_or_surplus_coverage_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE"
  refuses_report "incomplete coverage" "1 blocking finding(s) not covered" || return 1
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO" "$JUDGE_DECLINE_TWO"
  refuses_report "surplus line" "duplicate FINDING line(s) exceed" || return 1
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO" \
    "FINDING: policy rules/z.md:1 context-writing-style — decline — wrong: no such finding"
  refuses_report "unmatched line" "naming no blocking finding"
}

t_judge_fix_records_refuse() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_FIX_TWO"
  refuses_report "decline with fix" "1 blocking finding(s) not covered by a defer/decline ruling" || return 1
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_DUPLICATE"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_TWO" "$JUDGE_FIX_TWO"
  refuses_report "same finding decline and fix" "different verdicts"
}

t_judge_repeated_findings_dismiss() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_DUPLICATE"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_TWO" "$JUDGE_DECLINE_TWO"
  invoke_report
  assert_eq "exit" "0" "$RC" || return 1
  assert_eq "two follow-up entries" "2" "$(grep -c 'rules/b.md:7' "$COMMENT_BODY")" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_judge_carry_over() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$OLD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"rules/b.md"}]}'
  invoke_report
  assert_eq "changed path exit" "1" "$RC" || return 1
  assert_eq "uncovered" "rules/b.md" "$(jq -r '.uncovered[].path' <<<"$OUT")" || return 1
  assert_eq "compare judge head...head" "repos/owner/repo/compare/${OLD_SHA}...${HEAD_SHA}" "$(cat "$COMPARE_LOG")" || return 1
  assert_eq "changed path: nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')" || return 1
  MOCK_COMPARE='{"status":"ahead","files":[{"filename":"README.md"}]}'
  invoke_report
  assert_eq "unchanged path exit" "0" "$RC" || return 1
  assert_eq "comment then dismissal" "comment|dismiss" "$(cut -d' ' -f1 "$EVENTS" | paste -sd'|' -)"
}

t_judge_operator_only_grounds_refuse() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — out-of-scope #99: pre-existing on main; cost: a reword touches every rule file"
  refuses_report "wrong follow-up issue" "out-of-scope #99" || return 1
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — out-of-scope #${ISSUE}: pre-existing on main"
  refuses_report "missing cost ground" "need an operator ruling" || return 1
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — accept the risk until the next release"
  refuses_report "risk acceptance" "accept the risk" || return 1
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$DECLINE_TWO"
  refuses_report "groundless decline" "presentation only"
}

t_rule_names_do_not_refuse_judge() {
  local rule body
  for rule in no-secrets ci-safety; do
    body=$(golden_body "{\"summary\":\"Harmless finding\",\"findings\":[{\"path\":\"a.sh\",\"line\":1,\"rule\":\"$rule\",\"severity\":\"blocking\",\"message\":\"incorrectly applied rule\"}]}") || return 1
    set_review CHANGES_REQUESTED "$HEAD_SHA" "$body"
    write_judge_report "$HEAD_SHA" "FINDING: policy a.sh:1 $rule — decline — wrong: the cited clause does not apply"
    invoke_report
    assert_eq "$rule exit" "0" "$RC" || return 1
    assert_eq "$rule result" "dismissed" "$(jq -r .result <<<"$OUT")" || return 1
  done
}

t_judge_failing_check_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  MOCK_CHECKS='[{"name":"tests","bucket":"fail"}]'
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DECLINE_TWO"
  refuses_report "failing check" "failing check(s)"
}

t_judge_rerun_reuses_comment_then_noop() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_judge_report "$HEAD_SHA" "$JUDGE_DECLINE_ONE" "$JUDGE_DEFER_TWO"
  MOCK_COMMENT_RC=1
  invoke_report
  assert_eq "failed post exit" "2" "$RC" || return 1
  assert_eq "failed post: no dismissal" "0" "$(dismissals)" || return 1
  MOCK_COMMENT_RC=0
  invoke_report
  assert_eq "retry exit" "0" "$RC" || return 1
  MOCK_ISSUE_COMMENTS=$(jq -cn --rawfile b "$COMMENT_BODY" '[{"id":1,"body":$b}]')
  : > "$EVENTS"
  invoke_report
  assert_eq "re-run exit" "0" "$RC" || return 1
  assert_eq "re-run reuses the comment" "0" "$(comments)" || return 1
  assert_eq "re-run dismisses" "1" "$(dismissals)" || return 1
  set_review DISMISSED "$HEAD_SHA" "$BODY_TWO"
  : > "$EVENTS"
  invoke_report
  assert_eq "after dismissal" "noop" "$(jq -r .result <<<"$OUT")" || return 1
  assert_eq "after dismissal: nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_operator_ruling_unchanged() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" \
    "FINDING: policy rules/b.md:7 context-writing-style — defer — accept the risk until the next release"
  invoke_ruled
  assert_eq "exit" "0" "$RC" || return 1
  local digest; digest=$(digest_of_ruling)
  assert_eq "comment body" \
"Follow-up entries for owner/repo#5 at ${HEAD_SHA}, judge ruling ${digest}:

- \`skills/x/run.sh:3\` **error-handling** — declined, won't-fix: rule text misread; the harness sets it
- \`rules/b.md:7\` **context-writing-style** — deferred: accept the risk until the next release" \
    "$(cat "$COMMENT_BODY")" || return 1
  assert_eq "one dismissal" "1" "$(dismissals)"
}

t_other_authority_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  sed 's/^AUTHORITY: operator$/AUTHORITY: agent/' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "must be 'operator' or 'judge', not 'agent'" "the authority" || return 1
  assert_eq "nothing posted" "0" "$(wc -l < "$EVENTS" | tr -d ' ')"
}

t_missing_authority_refuses() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  grep -v '^AUTHORITY:' "$RULING" > "${RULING}.tmp" && mv "${RULING}.tmp" "$RULING"
  invoke_ruled
  assert_eq "exit" "1" "$RC" || return 1
  assert_unmet "AUTHORITY:" "the missing authority" || return 1
  assert_eq "no dismissal" "0" "$(dismissals)"
}

# End to end on the marker: the dismissal message this script actually sends
# is what poll-pr-reviews.sh must read as RULED and dismiss-stale-reviews.sh
# must accept as an all-clear. Each consumer runs in its own process against a
# PATH-stubbed gh serving the review list and a timeline carrying that message.
READER_STUB="${TMPDIR_TEST}/reader-stub"
mkdir -p "$READER_STUB" || { echo "fatal: cannot create $READER_STUB" >&2; exit 2; }
cat > "${READER_STUB}/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args="$*"
case "$1" in
  pr)
    case "$2" in
      view)   printf '{"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE","headRefOid":"%s"}\n' "$STUB_HEAD" ;;
      checks) echo '[]' ;;
      *) echo "reader stub: unsupported: $args" >&2; exit 2 ;;
    esac ;;
  api)
    if [[ "$2" == "graphql" ]]; then echo '[]'; exit 0; fi
    if [[ "$args" == *"-X PUT"* ]]; then echo "$args" >> "$STUB_LOG"; echo '{}'; exit 0; fi
    case "$args" in
      *'/actions/runs?'*) echo '{"total_count":0,"workflow_runs":[]}' ;;
      *timeline*) cat "$STUB_TIMELINE" ;;
      *reviews*)  cat "$STUB_REVIEWS" ;;
      *comments*) echo '[]' ;;
      *) echo "reader stub: unsupported: $args" >&2; exit 2 ;;
    esac ;;
  *) echo "reader stub: unsupported: $args" >&2; exit 2 ;;
esac
SH
chmod +x "${READER_STUB}/gh"

t_sent_marker_reads_ruled_and_sweeps() {
  set_review CHANGES_REQUESTED "$HEAD_SHA" "$BODY_TWO"
  write_ruling "$HEAD_SHA" "$DECLINE_ONE" "$DECLINE_TWO"
  invoke_ruled
  assert_eq "writer exit" "0" "$RC" || return 1
  local sent; sent=$(sed -n 's/^dismiss //p' "$EVENTS")
  [[ -n "$sent" ]] || { echo "    FAIL: no dismissal message captured" >&2; return 1; }
  jq -cn --arg head "$HEAD_SHA" \
    '[{"id":7,"user":{"login":"github-actions[bot]"},"state":"CHANGES_REQUESTED","commit_id":"0000","submitted_at":"2026-01-01T00:00:00Z","body":"older"},
      {"id":8,"user":{"login":"github-actions[bot]"},"state":"DISMISSED","commit_id":$head,"submitted_at":"2026-01-02T00:00:00Z","body":"ruled"}]' \
    > "${TMPDIR_TEST}/reader-reviews.json"
  jq -cn --arg m "$sent" '[{"event":"review_dismissed","dismissed_review":{"review_id":8,"state":"changes_requested","dismissal_message":$m}}]' \
    > "${TMPDIR_TEST}/reader-timeline.json"
  : > "${TMPDIR_TEST}/reader-log"
  local stub_env=(STUB_HEAD="$HEAD_SHA" STUB_REVIEWS="${TMPDIR_TEST}/reader-reviews.json"
                  STUB_TIMELINE="${TMPDIR_TEST}/reader-timeline.json" STUB_LOG="${TMPDIR_TEST}/reader-log"
                  PATH="${READER_STUB}:${PATH}")
  local snap swept
  snap=$(env "${stub_env[@]}" bash "${RELEASE_DIR}/poll-pr-reviews.sh" owner repo 5) \
    || { echo "    FAIL: poll exited non-zero" >&2; return 1; }
  assert_eq "poll reads RULED" "RULED" "$(jq -r '.reviews.codex.state' <<<"$snap")" || return 1
  swept=$(env "${stub_env[@]}" bash "${RELEASE_DIR}/dismiss-stale-reviews.sh" owner repo 5) \
    || { echo "    FAIL: dismiss-stale exited non-zero" >&2; return 1; }
  assert_eq "older CR swept" "7" "$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$swept")" || return 1
  grep -q "reviews/7/dismissals" "${TMPDIR_TEST}/reader-log" || { echo "    FAIL: sweep PUT not sent" >&2; return 1; }
}

# `run_suite`, not `main`: the sourced script under test owns `main`.
# Progress goes to stderr; stdout carries one JSON result.
run_suite() {
  echo "test_dismiss_ruled_review.sh" >&2
  run "covered: follow-up comment posted, then dismissal" t_all_covered_posts_followup_then_dismisses
  run "an existing follow-up comment is reused"       t_existing_followup_comment_is_reused
  run "a partial comment citing the digest is not reused" t_partial_comment_citing_digest_is_not_reused
  run "a failed follow-up post dismisses nothing"     t_failed_followup_post_dismisses_nothing
  run "operator records in PR without issue"          t_operator_records_in_pr_without_issue
  run "PR record read/post errors dismiss nothing"    t_pr_record_errors_dismiss_nothing
  run "PR record reused after dismissal error"        t_pr_record_reused_after_dismissal_error
  run "judge records in PR without issue argument"    t_judge_records_in_pr_without_issue_argument
  run "one uncovered finding refuses"                 t_one_uncovered_refuses
  run "a fix ruling leaves the finding uncovered"     t_fix_ruling_leaves_finding_uncovered
  run "a different line does not cover"               t_different_line_does_not_cover
  run "same path, different rule refuses"             t_same_path_different_rule_refuses
  run "a duplicate FINDING line refuses"              t_duplicate_finding_line_refuses
  run "an unmatched FINDING line refuses"             t_unmatched_finding_line_refuses
  run "same-line findings need matching ruling count" t_duplicate_review_finding_needs_matching_count
  run "same-line findings with one verdict dismiss"   t_duplicate_review_findings_with_same_verdict_dismiss
  run "same-line findings with mixed verdicts refuse"  t_duplicate_review_findings_with_mixed_verdicts_refuse
  run "rule names allow an operator ruling"           t_rule_names_do_not_refuse_operator
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
  run "a newer schema refuses, unmodified"            t_newer_schema_refuses_unmodified
  run "a version-2 ruling refuses, unmodified"        t_version_2_ruling_refuses_unmodified
  run "non-UTF-8 ruling bytes refuse, no traceback"   t_non_utf8_ruling_refuses_without_traceback
  run "a malformed ruling refuses"                    t_malformed_ruling_refuses
  run "list mode emits the blocking findings"         t_list_mode_emits_findings
  run "usage errors exit 2"                           t_usage_errors_exit_2
  run "a collected judge report dismisses"           t_judge_report_dismisses
  run "a judge decline dismisses"                     t_judge_decline_dismisses
  run "a missing or mismatched digest refuses"        t_judge_digest_missing_or_mismatched_refuses
  run "a report edited after collection refuses"      t_judge_report_edited_after_collection_refuses
  run "an unsupported judge schema refuses, unmodified" t_judge_unsupported_schema_refuses_unmodified
  run "a judge report without HEAD refuses"           t_judge_missing_head_refuses
  run "incomplete or surplus judge coverage refuses"  t_judge_incomplete_or_surplus_coverage_refuses
  run "judge fix records leave findings uncovered"    t_judge_fix_records_refuse
  run "repeated findings a judge rules each dismiss"  t_judge_repeated_findings_dismiss
  run "judge carry-over needs the path unchanged"     t_judge_carry_over
  run "operator-only grounds under judge refuse"      t_judge_operator_only_grounds_refuse
  run "rule names allow a judge ruling"               t_rule_names_do_not_refuse_judge
  run "a failing check refuses a judge ruling"        t_judge_failing_check_refuses
  run "a judge re-run reuses the comment, then noops" t_judge_rerun_reuses_comment_then_noop
  run "an operator ruling dismisses as before"        t_operator_ruling_unchanged
  run "another authority refuses"                     t_other_authority_refuses
  run "a missing AUTHORITY line refuses"              t_missing_authority_refuses
  run "the sent marker reads RULED and sweeps"        t_sent_marker_reads_ruled_and_sweeps
  printf '{"suite":"test_dismiss_ruled_review.sh","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ $FAIL_COUNT -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
