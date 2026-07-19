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
GH_CAPTURE=""
gh() {
  if [[ "${1:-}" == "api" && "$*" == */reviews* ]]; then
    cat > "$GH_CAPTURE"
    return 0
  fi
  return 0
}

run_main() { ( set -euo pipefail; main "$@" ); }

# --- pass, no findings -> COMMENT, body is the summary ---
t_pass() {
  local dir; dir=$(mktemp -d)
  GH_CAPTURE="$dir/payload.json"
  printf '{"summary":"Policy loaded: 21 rule files from rules/. All rules pass.","verdict":"pass","findings":[]}' > "$dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                              || { bad "pass: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "COMMENT" ]]              || { bad "pass: event COMMENT (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "0" ]]                 || { bad "pass: findings 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "COMMENT" ]]          || { bad "pass: payload event COMMENT"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "Policy loaded: 21"     || { bad "pass: body carries the load indicator"; rm -rf "$dir"; return; }
  ok "pass verdict -> COMMENT, summary body, 0 findings"
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
  [[ $rc -eq 1 ]] && ok "missing result file -> exit 1" || bad "missing result file -> exit 1 (rc=$rc)"
}

# --- invalid JSON -> exit 1 ---
t_invalid_json() {
  local dir; dir=$(mktemp -d)
  printf 'not json at all' > "$dir/final.json"
  local rc=0; run_main owner repo 1 "$dir/final.json" >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 1 ]] && ok "invalid JSON -> exit 1" || bad "invalid JSON -> exit 1 (rc=$rc)"
  rm -rf "$dir"
}

echo "== post-review.sh tests =="
t_pass
t_changes
t_missing_file
t_invalid_json
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
