#!/usr/bin/env bash
# Tests for post-review.sh — the mapping from Codex's structured result to the
# GitHub review payload (event + body). `gh` is mocked to capture the payload,
# so no network. Deterministic, hermetic (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_post_review.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/post-review.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: post-review.sh not readable at $SCRIPT" >&2; exit 2; }

# shellcheck source=/dev/null
source "$SCRIPT"
set +e  # relax errexit in the harness; each main() runs under its own set -e

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

# Mock gh: capture the /reviews POST payload (arrives on stdin via --input -).
# With MOCK_422_ON_APPROVE=1, an APPROVE submit fails HTTP 422 (as github-actions[bot]
# would), so the fallback-to-COMMENT path can be exercised. The review lookup
# (a GET) prints MOCK_REVIEWS, or fails like an API outage with MOCK_LOOKUP_FAIL=1;
# the timeline lookup prints MOCK_TIMELINE, or fails with MOCK_LOOKUP_FAIL=timeline.
# With MOCK_PERSISTED set to a file, GitHub keeps each POST as a review appended
# to that file, which the lookup also lists, but the response is lost (HTTP 502).
GH_CAPTURE=""
MOCK_PERSISTED=""
MOCK_422_ON_APPROVE=0
MOCK_REVIEWS='[]'
MOCK_TIMELINE='[]'
MOCK_LOOKUP_FAIL=0
REVIEWED_SHA=1111111111111111111111111111111111111111
LATER_HEAD_SHA=2222222222222222222222222222222222222222
OLDER_HEAD_SHA=3333333333333333333333333333333333333333
export REVIEWER_LOGIN="coding-policy-fleet-reviewer[bot]"
gh() {
  if [[ "${1:-}" == "api" && "$*" == */timeline* ]]; then
    [[ "$MOCK_LOOKUP_FAIL" != "timeline" ]] || { echo "gh: Server Error (HTTP 502)" >&2; return 1; }
    printf '%s\n' "$MOCK_TIMELINE"
    return 0
  fi
  if [[ "${1:-}" == "api" && "$*" == */reviews* && "$*" != *"--method POST"* ]]; then
    if [[ "$MOCK_LOOKUP_FAIL" == "1" ]]; then
      echo "gh: Server Error (HTTP 502)" >&2
      return 1
    fi
    printf '%s\n' "$MOCK_REVIEWS"
    [[ -z "$MOCK_PERSISTED" || ! -f "$MOCK_PERSISTED" ]] || cat "$MOCK_PERSISTED"
    return 0
  fi
  if [[ "${1:-}" == "api" && "$*" == */reviews* ]]; then
    local payload; payload=$(cat)
    if [[ -n "$MOCK_PERSISTED" ]]; then
      jq -c --arg login "$REVIEWER_LOGIN" '[{id: 2, user: {login: $login}, commit_id,
        state: {APPROVE: "APPROVED", REQUEST_CHANGES: "CHANGES_REQUESTED", COMMENT: "COMMENTED"}[.event]}]' \
        <<<"$payload" >> "$MOCK_PERSISTED"
      echo "gh: Bad Gateway (HTTP 502)" >&2
      return 1
    fi
    # GitHub defaults an omitted commit_id to the newer post-time head.
    jq --arg latest "$LATER_HEAD_SHA" '.commit_id //= $latest' <<<"$payload" > "$GH_CAPTURE"
    local ev; ev=$(jq -r '.event' <<<"$payload")
    if [[ "$MOCK_422_ON_APPROVE" == "1" && "$ev" == "APPROVE" ]]; then
      echo "gh: Unprocessable Entity (HTTP 422)" >&2
      return 1
    fi
    return 0
  fi
  return 0
}

run_main() { ( set -euo pipefail; main "$@" "$REVIEWED_SHA" ); }

# --- pass, no findings -> APPROVE (token can approve), body is the summary ---
t_pass() {
  local dir; dir=$(mktemp -d) || { bad "pass: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  printf '{"summary":"Policy loaded: 23 rule files from jbaruch/coding-policy. All rules pass.","findings":[]}' > "$dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                              || { bad "pass: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "APPROVE" ]]             || { bad "pass: event APPROVE (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "0" ]]                 || { bad "pass: findings 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "APPROVE" ]]         || { bad "pass: payload event APPROVE"; rm -rf "$dir"; return; }
  [[ "$(jq -r .commit_id "$GH_CAPTURE")" == "$REVIEWED_SHA" ]] || { bad "pass: a newer head must not inherit approval"; rm -rf "$dir"; return; }
  [[ "$(jq -r .head_sha <<<"$out")" == "$REVIEWED_SHA" ]] || { bad "pass: receipt must identify analyzed source"; rm -rf "$dir"; return; }
  # Assert the body begins with the load indicator (schema contract), not merely
  # that some line within it does — `grep -E "^..."` anchors per line, so a
  # preamble line before the summary would pass it. Here `=~` matches against the
  # whole body string (not line by line), so the pattern's leading `^` anchors to
  # the body's start.
  local body indicator_re='^Policy loaded: [0-9]+ rule files'
  body=$(jq -r .body "$GH_CAPTURE")
  [[ "$body" =~ $indicator_re ]]                              || { bad "pass: body begins with the load indicator"; rm -rf "$dir"; return; }
  ok "no findings -> APPROVE, summary body, 0 findings"
  rm -rf "$dir"
}

# --- pass but token can't approve (HTTP 422) -> falls back to COMMENT ---
t_pass_422_fallback() {
  local dir; dir=$(mktemp -d) || { bad "fallback: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=1
  printf '{"summary":"Policy loaded: 23 rule files from jbaruch/coding-policy. Clean.","findings":[]}' > "$dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json" 2>/dev/null)
  local rc=$?
  MOCK_422_ON_APPROVE=0
  [[ $rc -eq 0 ]]                                              || { bad "fallback: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "COMMENT" ]]             || { bad "fallback: output event COMMENT (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "COMMENT" ]]         || { bad "fallback: last payload event COMMENT"; rm -rf "$dir"; return; }
  [[ "$(jq -r .commit_id "$GH_CAPTURE")" == "$REVIEWED_SHA" ]] || { bad "fallback: COMMENT must retain the analyzed source"; rm -rf "$dir"; return; }
  ok "pass + APPROVE 422 -> falls back to COMMENT"
  rm -rf "$dir"
}

# --- a blocking finding -> REQUEST_CHANGES, findings in a Blocking section ---
t_blocking() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON'
{"summary":"Policy loaded: 23 rule files from rules/. One violation.",
 "findings":[{"path":"skills/x/run.sh","line":3,"rule":"error-handling","severity":"blocking","message":"missing set -euo pipefail; add it at the top"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "blocking: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "blocking: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "1" ]]                       || { bad "blocking: findings 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .commit_id "$GH_CAPTURE")" == "$REVIEWED_SHA" ]] || { bad "blocking: findings must retain the analyzed source"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "blocking: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"        || { bad "blocking: body has a Blocking section"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "skills/x/run.sh:3"           || { bad "blocking: body cites the finding path:line"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "error-handling"             || { bad "blocking: body names the rule"; rm -rf "$dir"; return; }
  ok "blocking finding -> REQUEST_CHANGES, Blocking section in body"
  rm -rf "$dir"
}

# --- advisory-only findings -> APPROVE (clears an older block), Advisory section ---
t_advisory_only() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  cat > "$dir/final.json" <<'JSON'
{"summary":"Policy loaded: 23 rule files from rules/. Optional hardening only.",
 "findings":[{"path":"rules/foo.md","line":8,"rule":"simplicity","severity":"advisory","message":"[optional hardening] Add a versioned recovery journal for hypothetical future consumers; no demonstrated defect or binding obligation."}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "advisory: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "APPROVE" ]]                    || { bad "advisory: event APPROVE (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "1" ]]                       || { bad "advisory: findings 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "0" ]]                       || { bad "advisory: blocking 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .advisory <<<"$out")" == "1" ]]                       || { bad "advisory: advisory 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "APPROVE" ]]               || { bad "advisory: payload event APPROVE"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Advisory findings"        || { bad "advisory: body has an Advisory section"; rm -rf "$dir"; return; }
  if jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"; then bad "advisory: body must not have a Blocking section"; rm -rf "$dir"; return; fi
  ok "advisory-only -> APPROVE (never gates), Advisory section"
  rm -rf "$dir"
}

# --- mixed blocking + advisory -> REQUEST_CHANGES, both sections present ---
t_mixed() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON'
{"summary":"Policy loaded: 23 rule files from rules/. Mixed.",
 "findings":[
   {"path":"a.sh","line":1,"rule":"no-secrets","severity":"blocking","message":"hardcoded token; move to env"},
   {"path":"rules/b.md","line":2,"rule":"context-writing-style","severity":"advisory","message":"prefer a synonym"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "mixed: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "mixed: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "mixed: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .advisory <<<"$out")" == "1" ]]                       || { bad "mixed: advisory 1 (got $out)"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"        || { bad "mixed: body has a Blocking section"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Advisory findings"        || { bad "mixed: body has an Advisory section"; rm -rf "$dir"; return; }
  ok "mixed blocking+advisory -> REQUEST_CHANGES, both sections"
  rm -rf "$dir"
}

# --- missing severity is treated as blocking (fail-safe) ---
t_missing_severity_blocks() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON'
{"summary":"Policy loaded: 23 rule files from rules/. Unclassified finding.",
 "findings":[{"path":"x.sh","line":1,"rule":"error-handling","message":"no severity field"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "missing-severity: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "missing-severity: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "missing-severity: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  ok "missing severity -> treated as blocking (fail-safe)"
  rm -rf "$dir"
}

# --- missing result file -> exit 1 ---
t_missing_file() {
  local rc=0; run_main owner repo 1 "/nonexistent/final.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "missing result file -> exit 1"; else bad "missing result file -> exit 1 (rc=$rc)"; fi
}

# --- invalid JSON -> exit 1 ---
t_invalid_json() {
  local dir; dir=$(mktemp -d) || { bad "invalid_json: mktemp -d failed"; return; }
  printf 'not json at all' > "$dir/final.json"
  local rc=0; run_main owner repo 1 "$dir/final.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "invalid JSON -> exit 1"; else bad "invalid JSON -> exit 1 (rc=$rc)"; fi
  rm -rf "$dir"
}

t_missing_or_invalid_source() {
  local dir; dir=$(mktemp -d) || exit 2
  GH_CAPTURE="$dir/payload.json"
  printf '{"summary":"Clean","findings":[]}' > "$dir/final.json"
  local rc=0
  (set -euo pipefail; main owner repo 1 "$dir/final.json") >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 && ! -f "$GH_CAPTURE" ]]; then ok "missing source refuses to post";
  else bad "missing source was not refused (rc=$rc)"; fi
  rc=0
  (set -euo pipefail; main owner repo 1 "$dir/final.json" invalid) >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 && ! -f "$GH_CAPTURE" ]]; then ok "invalid source refuses to post";
  else bad "invalid source was not refused (rc=$rc)"; fi
  rm -rf "$dir"
}

# One review by <login> in <state> on <commit>, as the lookup's only page.
review_page() {
  jq -cn --arg login "$1" --arg state "$2" --arg commit "$3" \
    '[{"id":1,"user":{"login":$login},"state":$state,"commit_id":$commit,"body":"earlier verdict"}]'
}

# How GitHub's timeline records review 1's dismissal: by dismiss-ruled-review.sh,
# and by GitHub's automatic stale-review dismissal.
RULED_TIMELINE='[{"event":"review_dismissed","dismissed_review":{"review_id":1,"dismissal_message":"JUDGE-RULED: 0123456789abcdef covers 1 blocking findings at head; tracked in #9"}}]'
STALE_TIMELINE='[{"event":"review_dismissed","dismissed_review":{"review_id":1,"dismissal_message":null}}]'

# --- the first verdict on a head stands: a later run on that head posts nothing ---
t_head_already_reviewed() {
  local state dir out rc
  for state in APPROVED CHANGES_REQUESTED DISMISSED; do
    dir=$(mktemp -d) || { bad "already reviewed: mktemp -d failed"; return; }
    GH_CAPTURE="$dir/payload.json"; MOCK_TIMELINE="$RULED_TIMELINE"
    # The matching review sits on the second page, so pagination is read whole.
    MOCK_REVIEWS="$(review_page "$REVIEWER_LOGIN" COMMENTED "$OLDER_HEAD_SHA")"$'\n'"$(review_page "$REVIEWER_LOGIN" "$state" "$REVIEWED_SHA")"
    printf '{"summary":"Policy loaded: 23 rule files. One violation.","findings":[{"path":"a.sh","line":1,"rule":"error-handling","severity":"blocking","message":"m"}]}' > "$dir/final.json"
    rc=0; out=$(run_main owner repo 5 "$dir/final.json" 2>"$dir/err") || rc=$?
    MOCK_REVIEWS='[]'; MOCK_TIMELINE='[]'
    if [[ $rc -eq 0 && ! -f "$GH_CAPTURE" && "$(jq -r .state <<<"$out")" == "already_reviewed" ]] \
       && grep -q "already reviewed owner/repo#5 at ${REVIEWED_SHA}" "$dir/err"; then
      ok "existing ${state} review on the head -> posts nothing, exit 0, logged"
    else
      bad "existing ${state} review on the head must suppress the post (rc=$rc, out=$out)"
    fi
    rm -rf "$dir"
  done
}

# --- an older head's review, another reviewer's on this head, or a stale-
# dismissed one on this head does not count ---
t_other_reviews_do_not_suppress() {
  local dir out rc label
  for label in older-head other-reviewer stale-dismissed; do
    dir=$(mktemp -d) || { bad "other reviews: mktemp -d failed"; return; }
    GH_CAPTURE="$dir/payload.json"
    case "$label" in
      older-head) MOCK_REVIEWS=$(review_page "$REVIEWER_LOGIN" APPROVED "$OLDER_HEAD_SHA") ;;
      other-reviewer) MOCK_REVIEWS=$(review_page "jbaruch-claude-reviewer[bot]" APPROVED "$REVIEWED_SHA") ;;
      stale-dismissed) MOCK_REVIEWS=$(review_page "$REVIEWER_LOGIN" DISMISSED "$REVIEWED_SHA"); MOCK_TIMELINE="$STALE_TIMELINE" ;;
    esac
    printf '{"summary":"Policy loaded: 23 rule files. Clean.","findings":[]}' > "$dir/final.json"
    rc=0; out=$(run_main owner repo 5 "$dir/final.json") || rc=$?
    MOCK_REVIEWS='[]'; MOCK_TIMELINE='[]'
    if [[ $rc -eq 0 && "$(jq -r .state <<<"$out")" == "posted" && "$(jq -r .commit_id "$GH_CAPTURE" 2>/dev/null)" == "$REVIEWED_SHA" ]]; then
      ok "${label} review -> the verdict is posted"
    else
      bad "${label} review must not suppress the post (rc=$rc, out=$out)"
    fi
    rm -rf "$dir"
  done
}

# --- a failed review or dismissal lookup fails visibly and posts nothing ---
t_lookup_failure_refuses_to_post() {
  local dir rc mode expect
  for mode in 1 timeline; do
    dir=$(mktemp -d) || { bad "lookup failure: mktemp -d failed"; return; }
    GH_CAPTURE="$dir/payload.json"; MOCK_LOOKUP_FAIL=$mode; rc=0
    MOCK_REVIEWS=$(review_page "$REVIEWER_LOGIN" DISMISSED "$REVIEWED_SHA")
    if [[ "$mode" == 1 ]]; then expect="could not list the reviews"; else expect="could not read how review 1"; fi
    printf '{"summary":"Policy loaded: 23 rule files. Clean.","findings":[]}' > "$dir/final.json"
    run_main owner repo 5 "$dir/final.json" >/dev/null 2>"$dir/err" || rc=$?
    MOCK_LOOKUP_FAIL=0; MOCK_REVIEWS='[]'
    if [[ $rc -ne 0 && ! -f "$GH_CAPTURE" ]] && grep -q "$expect" "$dir/err"; then
      ok "failed ${mode/1/review} lookup -> non-zero exit, nothing posted"
    else
      bad "failed ${mode/1/review} lookup must fail without posting (rc=$rc)"
    fi
    rm -rf "$dir"
  done
}

# --- an unparseable lookup response fails visibly and posts nothing ---
t_lookup_garbage_refuses_to_post() {
  local dir rc=0
  dir=$(mktemp -d) || { bad "lookup garbage: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_REVIEWS='<html>not json</html>'
  printf '{"summary":"Policy loaded: 23 rule files. Clean.","findings":[]}' > "$dir/final.json"
  run_main owner repo 5 "$dir/final.json" >/dev/null 2>&1 || rc=$?
  MOCK_REVIEWS='[]'
  if [[ $rc -ne 0 && ! -f "$GH_CAPTURE" ]]; then ok "unparseable review list -> non-zero exit, nothing posted";
  else bad "unparseable review list must fail without posting (rc=$rc)"; fi
  rm -rf "$dir"
}

# --- a POST GitHub kept but whose response was lost is not posted again ---
t_lost_response_posts_once() {
  local dir out rc=0
  dir=$(mktemp -d) || { bad "lost response: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_PERSISTED="$dir/persisted"
  printf '{"summary":"Policy loaded: 23 rule files. Clean.","findings":[]}' > "$dir/final.json"
  out=$(run_main owner repo 5 "$dir/final.json" 2>/dev/null) || rc=$?
  MOCK_PERSISTED=""
  if [[ $rc -eq 0 && "$(jq -r .state <<<"$out")" == "already_reviewed" && "$(wc -l < "$dir/persisted")" -eq 1 ]]; then
    ok "lost POST response -> the retry finds the kept review, exactly one persisted"
  else
    bad "lost POST response must persist exactly one review (rc=$rc, out=$out, persisted=$(wc -l < "$dir/persisted"))"
  fi
  rm -rf "$dir"
}

# --- without REVIEWER_LOGIN the identity is unknown: refuse before any API call ---
t_missing_reviewer_login() {
  local dir rc=0
  dir=$(mktemp -d) || { bad "missing login: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"
  printf '{"summary":"Clean","findings":[]}' > "$dir/final.json"
  (unset REVIEWER_LOGIN; run_main owner repo 5 "$dir/final.json") >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 && ! -f "$GH_CAPTURE" ]]; then ok "missing REVIEWER_LOGIN -> exit 2, nothing posted";
  else bad "missing REVIEWER_LOGIN must refuse (rc=$rc)"; fi
  rm -rf "$dir"
}

echo "== post-review.sh tests =="
t_pass
t_pass_422_fallback
t_blocking
t_advisory_only
t_mixed
t_missing_severity_blocks
t_missing_file
t_invalid_json
t_missing_or_invalid_source
t_head_already_reviewed
t_other_reviews_do_not_suppress
t_lookup_failure_refuses_to_post
t_lookup_garbage_refuses_to_post
t_lost_response_posts_once
t_missing_reviewer_login
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
