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
# would), so the fallback-to-COMMENT path can be exercised.
GH_CAPTURE=""
MOCK_422_ON_APPROVE=0
gh() {
  if [[ "${1:-}" == "api" && "$*" == */reviews* ]]; then
    local payload; payload=$(cat)
    printf '%s' "$payload" > "$GH_CAPTURE"
    local ev; ev=$(jq -r '.event' <<<"$payload")
    if [[ "$MOCK_422_ON_APPROVE" == "1" && "$ev" == "APPROVE" ]]; then
      echo "gh: Unprocessable Entity (HTTP 422)" >&2
      return 1
    fi
    return 0
  fi
  return 0
}

run_main() { ( set -euo pipefail; main "$@" ); }

# --- pass, no findings -> APPROVE (token can approve), body is the summary ---
t_pass() {
  local dir; dir=$(mktemp -d) || { bad "pass: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  printf '{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy. All rules pass.","verdict":"pass","findings":[]}' > "$dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                              || { bad "pass: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "APPROVE" ]]             || { bad "pass: event APPROVE (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "0" ]]                 || { bad "pass: findings 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "APPROVE" ]]         || { bad "pass: payload event APPROVE"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "Policy loaded: 21"     || { bad "pass: body carries the load indicator"; rm -rf "$dir"; return; }
  ok "pass verdict -> APPROVE, summary body, 0 findings"
  rm -rf "$dir"
}

# --- pass but token can't approve (HTTP 422) -> falls back to COMMENT ---
t_pass_422_fallback() {
  local dir; dir=$(mktemp -d) || { bad "fallback: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=1
  printf '{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy. Clean.","verdict":"pass","findings":[]}' > "$dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json" 2>/dev/null)
  local rc=$?
  MOCK_422_ON_APPROVE=0
  [[ $rc -eq 0 ]]                                              || { bad "fallback: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "COMMENT" ]]             || { bad "fallback: output event COMMENT (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "COMMENT" ]]         || { bad "fallback: last payload event COMMENT"; rm -rf "$dir"; return; }
  ok "pass + APPROVE 422 -> falls back to COMMENT"
  rm -rf "$dir"
}

# --- changes_requested with findings -> REQUEST_CHANGES, findings in body ---
t_changes() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON'
{"summary":"Policy loaded: 21 rule files from rules/. One violation.","verdict":"changes_requested",
 "findings":[{"path":"skills/x/run.sh","line":3,"rule":"error-handling","message":"missing set -euo pipefail; add it at the top"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "changes: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "changes: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "1" ]]                       || { bad "changes: findings 1 (got $out)"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "skills/x/run.sh:3"           || { bad "changes: body cites the finding path:line"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "error-handling"             || { bad "changes: body names the rule"; rm -rf "$dir"; return; }
  ok "changes_requested -> REQUEST_CHANGES, findings in body"
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

echo "== post-review.sh tests =="
t_pass
t_pass_422_fallback
t_changes
t_missing_file
t_invalid_json
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
