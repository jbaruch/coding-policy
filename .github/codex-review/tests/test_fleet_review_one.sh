#!/usr/bin/env bash
# Tests for fleet-review-one.sh — the single-PR review orchestration. git, tessl,
# and codex are faked on PATH; the CENTRAL_DIR driver scripts are stubbed, so no
# network and no real Codex. Deterministic, hermetic (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_fleet_review_one.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/fleet-review-one.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: fleet-review-one.sh not readable at $SCRIPT" >&2; exit 2; }

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

# Best-effort cleanup that warns on failure rather than swallowing it
# (rules/error-handling.md — warn to stderr, never nothing).
rmwarn() { rm -rf "$@" || echo "test_fleet_review_one: warning: could not remove ${*}" >&2; }

# Build a fake toolchain (git/tessl/codex) + stubbed CENTRAL_DIR driver scripts.
# Echoes: BIN CENTRAL CODEXH (space-separated) for the caller to consume.
# The function body is a `set -e` subshell so ANY failed mktemp/cat/chmod/mkdir
# during fixture setup aborts non-zero — the caller propagates it via
# `env_line=$(make_env) || exit 2` (rules/error-handling.md aggregate carve-out:
# a setup step that could corrupt the run carries its own failure check).
make_env() (
  set -e
  local bin central codexh
  bin=$(mktemp -d)
  central=$(mktemp -d)
  codexh=$(mktemp -d)

  cat > "$bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# clone <flags..> <url> <dest>: create an empty checkout at dest; everything else is a no-op.
if [ "${1:-}" = "clone" ]; then dest="${!#}"; mkdir -p "$dest/.git"; fi
exit 0
EOF
  printf '#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n' > "$bin/tessl"
  cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# Record argv one arg per line (so the test can assert the model pin), then
# write the canned structured result to the --output-last-message path.
if [ -n "${CODEX_ARGV_LOG:-}" ]; then printf '%s\n' "$@" > "$CODEX_ARGV_LOG"; fi
out=""
while [ $# -gt 0 ]; do [ "$1" = "--output-last-message" ] && out="${2:-}"; shift; done
[ -n "$out" ] && printf '{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy.","findings":[]}' > "$out"
exit 0
EOF
  chmod +x "$bin"/*

  # Fleet prompt/schema + the shared drivers all live under .github/codex-review.
  mkdir -p "$central/.github/codex-review"
  echo '{}'     > "$central/.github/codex-review/fleet-schema.json"
  echo 'prompt' > "$central/.github/codex-review/fleet-prompt.md"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n' > "$central/.github/codex-review/assert-no-secret-leak.sh"
  cat > "$central/.github/codex-review/post-review.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# args: <owner> <repo> <pr> <result-json> — echo them back so the test can assert routing.
printf '{"state":"posted","owner":"%s","repo":"%s","pr":"%s"}\n' "$1" "$2" "$3"
EOF

  echo '{"tokens":{"access_token":"x"}}' > "$codexh/auth.json"
  printf '%s %s %s' "$bin" "$central" "$codexh"
)

# --- happy path: routes to the poster with the right owner/repo/pr ---
t_happy() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local out rc
  out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
        bash "$SCRIPT" jbaruch repo-a 7 main 2>/dev/null); rc=$?
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 0 ]]                                        || { bad "happy: exit 0 (rc=$rc, out=$out)"; return; }
  [[ "$(jq -r .state <<<"$out")" == "posted" ]]         || { bad "happy: state posted ($out)"; return; }
  [[ "$(jq -r .owner <<<"$out")" == "jbaruch" ]]        || { bad "happy: owner routed ($out)"; return; }
  [[ "$(jq -r .repo  <<<"$out")" == "repo-a" ]]         || { bad "happy: repo routed ($out)"; return; }
  [[ "$(jq -r .pr    <<<"$out")" == "7" ]]              || { bad "happy: pr routed ($out)"; return; }
  ok "happy path routes to the poster with owner/repo/pr"
}

# The reviewer's model pin. Renewing the pin in fleet-review-one.sh means
# renewing it here too — a dropped or misspelled flag must fail this suite.
PIN_MODEL="gpt-5.6-sol"
PIN_EFFORT='model_reasoning_effort="high"'

# has_pair <argv-log> <flag> <value>: true when <flag> is immediately followed
# by <value> in the recorded argv (one arg per line).
has_pair() {
  local log="$1" flag="$2" value="$3" prev="" arg
  while IFS= read -r arg; do
    [[ "$prev" == "$flag" && "$arg" == "$value" ]] && return 0
    prev="$arg"
  done < "$log"
  return 1
}

# --- codex runs on the pinned model at the pinned effort ---
t_model_pin() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local log="$CODEXH/argv.log" rc=0 model_ok=1 effort_ok=1
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" CODEX_ARGV_LOG="$log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ -f "$log" ]]; then
    has_pair "$log" --model "$PIN_MODEL" && model_ok=0
    has_pair "$log" -c "$PIN_EFFORT" && effort_ok=0
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 0 ]]        || { bad "model_pin: exit 0 (rc=$rc)"; return; }
  [[ $model_ok -eq 0 ]]  || { bad "model_pin: codex argv carries --model $PIN_MODEL"; return; }
  [[ $effort_ok -eq 0 ]] || { bad "model_pin: codex argv carries -c $PIN_EFFORT"; return; }
  ok "codex runs with --model $PIN_MODEL and -c $PIN_EFFORT"
}

# --- wrong arg count -> exit 2 ---
t_bad_args() {
  local rc=0; bash "$SCRIPT" only three args >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 ]]; then ok "wrong arg count -> exit 2"; else bad "bad_args: expected exit 2 (rc=$rc)"; fi
}

# --- a missing CENTRAL_DIR driver file -> exit 1 ---
t_missing_driver() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  rm -f "$CENTRAL/.github/codex-review/fleet-schema.json"
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  if [[ $rc -eq 1 ]]; then ok "missing driver file -> exit 1"; else bad "missing_driver: expected exit 1 (rc=$rc)"; fi
}

# --- missing GH_TOKEN -> non-zero (unset :? guard) ---
t_missing_token() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local rc=0
  PATH="$BIN:$PATH" CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  if [[ $rc -ne 0 ]]; then ok "missing GH_TOKEN -> non-zero"; else bad "missing_token: expected non-zero"; fi
}

echo "== fleet-review-one.sh tests =="
t_happy
t_model_pin
t_bad_args
t_missing_driver
t_missing_token
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
