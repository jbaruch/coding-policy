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
if [ "${1:-}" = show ] && [ "${2:-}" = origin/main:tessl.json ] && [ -n "${FAKE_BASE_MANIFEST:-}" ]; then
  cat "$FAKE_BASE_MANIFEST"; exit 0
fi
# FAKE_BASE_DIR holds base-ref files: `ls-tree --name-only origin/main -- <p>`
# prints <p> when present, and `show origin/main:<p>` prints its content.
if [ -n "${FAKE_BASE_DIR:-}" ]; then
  if [ "${1:-}" = ls-tree ] && [ "${3:-}" = origin/main ]; then
    if [ -f "$FAKE_BASE_DIR/${5:-}" ]; then printf '%s\n' "$5"; fi
    exit 0
  fi
  if [ "${1:-}" = show ] && [ "${2#origin/main:}" != "${2:-}" ]; then
    cat "$FAKE_BASE_DIR/${2#origin/main:}"; exit $?
  fi
fi
# Like real git, showing a base-ref path with no fixture behind it fails.
if [ "${1:-}" = show ]; then echo "fatal: path '${2#*:}' does not exist" >&2; exit 128; fi
if [ "${1:-}" = diff ] && [ "${2:-}" = --check ]; then exit "${FAKE_DIFF_CHECK_RC:-0}"; fi
if [ "${1:-}" = "rev-parse" ]; then
  if [ -n "${FAKE_HEAD_FILE:-}" ]; then cat "$FAKE_HEAD_FILE";
  else printf '%s\n' "${FAKE_HEAD_SHA:-1111111111111111111111111111111111111111}"; fi
fi
exit 0
EOF
  cat > "$bin/tessl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${TESSL_LOG:-}" ]; then printf '%s\n' "$*" >> "$TESSL_LOG"; fi
if [ "${1:-}" = install ]; then
  spec="${!#}"; name="${spec%@*}"
  if [ -n "${TESSL_BLOCK:-}" ] && [ "$name" = "$TESSL_BLOCK" ]; then
    printf '✘ Blocked by install policy (not added to tessl.json):\n   ✘  Security  Critical\n'
    exit 0
  fi
  if [ -n "${TESSL_FAIL:-}" ] && [ "$name" = "$TESSL_FAIL" ]; then
    printf 'plugin not found\n'
    exit 3
  fi
  mkdir -p ".tessl/plugins/$name"
fi
exit 0
EOF
  cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# Record argv one arg per line (so the test can assert the model pin), then
# write the canned structured result to the --output-last-message path.
if [ -n "${CODEX_ARGV_LOG:-}" ]; then printf '%s\n' "$@" > "$CODEX_ARGV_LOG"; fi
if [ -n "${CODEX_PROMPT_LOG:-}" ]; then cat > "$CODEX_PROMPT_LOG"; else cat >/dev/null; fi
out=""
while [ $# -gt 0 ]; do [ "$1" = "--output-last-message" ] && out="${2:-}"; shift; done
[ -n "$out" ] && printf '{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy.","findings":[]}' > "$out"
if [ -n "${FAKE_HEAD_FILE:-}" ]; then printf '%s\n' 2222222222222222222222222222222222222222 > "$FAKE_HEAD_FILE"; fi
exit 0
EOF
  chmod +x "$bin"/*

  # Fleet prompt/schema + the shared drivers all live under .github/codex-review.
  mkdir -p "$central/.github/codex-review"
  cp "$(dirname "$SCRIPT")/../reviewer_failure.py" "$central/.github/reviewer_failure.py"
  echo '{}'     > "$central/.github/codex-review/fleet-schema.json"
  echo 'prompt' > "$central/.github/codex-review/fleet-prompt.md"
  cat > "$central/.github/codex-review/prior-feedback.py" <<'PY'
print('{"status":"available","items":[]}')
PY
  cat > "$central/.github/codex-review/policy-authoring-context.py" <<'PY'
print('{"schema_version":1,"eligible":false,"rules":[]}')
PY
  printf '#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n' > "$central/.github/codex-review/assert-no-secret-leak.sh"
  cat > "$central/.github/codex-review/post-review.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# args: <owner> <repo> <pr> <result-json> <reviewed-sha>.
printf '{"state":"posted","owner":"%s","repo":"%s","pr":"%s","head_sha":"%s"}\n' "$1" "$2" "$3" "$5"
EOF

  cat > "$central/.github/codex-review/materialize-core-authority.py" <<'PYTEST'
import os
from pathlib import Path
import sys
if os.environ.get("CORE_CONTEXT_LOG"):
    Path(os.environ["CORE_CONTEXT_LOG"]).write_text(str(Path.cwd()) + "\n" + str(Path(sys.argv[1]).resolve()))
raise SystemExit(int(os.environ.get("CORE_CONTEXT_RC", "0")))
PYTEST

  echo '{"tokens":{"access_token":"x"}}' > "$codexh/auth.json"
  printf '%s %s %s' "$bin" "$central" "$codexh"
)

# --- happy path: routes to the poster with the right owner/repo/pr ---
t_happy() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf '%s\n' 1111111111111111111111111111111111111111 > "$CODEXH/head" || exit 2
  local out rc
  out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_HEAD_FILE="$CODEXH/head" \
        bash "$SCRIPT" jbaruch repo-a 7 main 2>/dev/null); rc=$?
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 0 ]]                                        || { bad "happy: exit 0 (rc=$rc, out=$out)"; return; }
  [[ "$(jq -r .state <<<"$out")" == "posted" ]]         || { bad "happy: state posted ($out)"; return; }
  [[ "$(jq -r .owner <<<"$out")" == "jbaruch" ]]        || { bad "happy: owner routed ($out)"; return; }
  [[ "$(jq -r .repo  <<<"$out")" == "repo-a" ]]         || { bad "happy: repo routed ($out)"; return; }
  [[ "$(jq -r .pr    <<<"$out")" == "7" ]]              || { bad "happy: pr routed ($out)"; return; }
  [[ "$(jq -r .head_sha <<<"$out")" == "1111111111111111111111111111111111111111" ]] || { bad "happy: head advance retargeted the verdict ($out)"; return; }
  ok "head advance during analysis retains the original reviewed source"
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

t_nonzero_whitespace_still_posts_review() {
  local check_rc env_line out rc prompt saw_status
  for check_rc in 2 128; do
    env_line=$(make_env) || exit 2
    read -r BIN CENTRAL CODEXH <<< "$env_line"
    prompt="$CODEXH/prompt.txt"
    rc=0
    out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
      FAKE_DIFF_CHECK_RC="$check_rc" CODEX_PROMPT_LOG="$prompt" \
      bash "$SCRIPT" jbaruch repo-a 7 main 2>/dev/null) || rc=$?
    saw_status=1
    if [[ -f "$prompt" ]] && grep -q needs_inspection "$prompt"; then saw_status=0; fi
    rmwarn "$BIN" "$CENTRAL" "$CODEXH"
    [[ $rc -eq 0 && "$out" == *'"state":"posted"'* && $saw_status -eq 0 ]] \
      || { bad "whitespace exit $check_rc must reach review with an explicit inspection result"; return; }
  done
  ok "nonzero whitespace and tool errors cannot strand the policy review"
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

t_invalid_authoring_context() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf 'raise SystemExit(2)\n' > "$CENTRAL/.github/codex-review/policy-authoring-context.py" || exit 2
  local log="$CODEXH/codex.log" rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" CODEX_ARGV_LOG="$log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -ne 0 && ! -f "$log" ]]; then
    ok "unusable authoring context stops before Codex"
  else
    bad "invalid authoring context reached review (rc=$rc)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_invalid_source_sha() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local log="$CODEXH/codex.log" rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_HEAD_SHA=invalid CODEX_ARGV_LOG="$log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -ne 0 && ! -f "$log" ]]; then ok "invalid source SHA stops before review";
  else bad "invalid source SHA reached review (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
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

t_core_context_from_trusted_base() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"latest"}}}' > "$CODEXH/base.json" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    FAKE_BASE_MANIFEST="$CODEXH/base.json" CORE_CONTEXT_LOG="$CODEXH/core.log" \
    CODEX_ARGV_LOG="$CODEXH/codex.log" bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 && -s "$CODEXH/core.log" && -s "$CODEXH/codex.log" ]]; then
    local context_cwd context_arg
    context_cwd=$(sed -n '1p' "$CODEXH/core.log")
    context_arg=$(sed -n '2p' "$CODEXH/core.log")
    if [[ "$context_cwd" == "$context_arg" ]]; then ok "trusted-base Core uses the isolated materializer before review";
    else bad "Core materializer used a different workspace"; fi
  else bad "trusted Core missing or review failed (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_core_context_refusal_stops_review() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"latest"}}}' > "$CODEXH/base.json" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    FAKE_BASE_MANIFEST="$CODEXH/base.json" CORE_CONTEXT_RC=2 CODEX_ARGV_LOG="$CODEXH/codex.log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -ne 0 && ! -e "$CODEXH/codex.log" ]]; then ok "Core context refusal stops before reviewer execution";
  else bad "missing Core authority reached review (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_no_core_without_base_enrollment() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf '{"dependencies":{}}' > "$CODEXH/base.json" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    FAKE_BASE_MANIFEST="$CODEXH/base.json" CORE_CONTEXT_LOG="$CODEXH/core.log" \
    CODEX_ARGV_LOG="$CODEXH/codex.log" bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 && ! -e "$CODEXH/core.log" && -s "$CODEXH/codex.log" ]]; then ok "no Core exception without trusted-base enrollment";
  else bad "Core exception selected outside trusted base (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

# --- #817: extra manifests named in the base ref's manifest list ---
# Stage a base ref whose .github/fleet-review-manifests lists
# tessl-workspace/tessl.json, run a review, and leave BIN CENTRAL CODEXH BASE
# set for the caller's assertions.
# $1: list body. $2: the extra manifest's body, or "" to leave it off the base.
# Extra environment (TESSL_BLOCK) passes through to the fake tessl.
run_with_marker() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  BASE="$CODEXH/base"
  mkdir -p "$BASE/.github" || exit 2
  printf '%s' "$1" > "$BASE/.github/fleet-review-manifests" || exit 2
  if [[ -n "$2" ]]; then
    mkdir -p "$BASE/tessl-workspace" || exit 2
    printf '%s' "$2" > "$BASE/tessl-workspace/tessl.json" || exit 2
  fi
  RC=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_BASE_DIR="$BASE" \
    TESSL_LOG="$CODEXH/tessl.log" CODEX_ARGV_LOG="$CODEXH/codex.log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>"$CODEXH/err" || RC=$?
}

t_extra_manifest_installs_its_plugins() {
  run_with_marker $'# Opts this repository in.\ntessl-workspace/tessl.json\n' '{"dependencies":{"acme/provider":{"version":"1.0.0"}}}'
  if [[ $RC -eq 0 ]] && grep -qxF 'install --strict acme/provider@1.0.0' "$CODEXH/tessl.log"; then ok "a listed base manifest installs its plugins at the pinned version"
  else bad "extra manifest plugins not installed at the pinned version (rc=$RC, log: $(cat "$CODEXH/tessl.log"))"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_extra_manifest_absent_on_base_stops_review() {
  run_with_marker $'tessl-workspace/tessl.json\n' ''
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF 'tessl-workspace/tessl.json (listed in' "$CODEXH/err" && grep -qF 'only the base ref is trusted' "$CODEXH/err"; then
    ok "a listed manifest missing from the base stops the review before Codex"
  else bad "head-only manifest reached review (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_comment_only_marker_installs_only_policy() {
  run_with_marker $'# Provider manifests for the fleet reviewer.\n#\n' ''
  if [[ $RC -eq 0 ]] && [[ "$(cat "$CODEXH/tessl.log")" == "install jbaruch/coding-policy" ]]; then ok "a comment-only manifest list behaves as before"
  else bad "comment-only manifest list changed the plugin set (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_escaping_marker_line_stops_review() {
  run_with_marker $'../other/tessl.json\n' ''
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF "'../other/tessl.json' (in" "$CODEXH/err"; then ok "a list entry that is not a plain relative path stops the review"
  else bad "escaping list entry reached review (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_policy_blocked_install_stops_review() {
  TESSL_BLOCK=acme/provider run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/provider":{"version":"latest"}}}'
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF 'acme/provider' "$CODEXH/err" && grep -qF 'Blocked by install policy' "$CODEXH/err"; then
    ok "a plugin tessl refuses while exiting 0 stops the review with its reason"
  else bad "silently refused install reached review or hid its reason (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_failed_install_without_reason_line_stops_review() {
  TESSL_FAIL=acme/provider run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/provider":{"version":"latest"}}}'
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF 'acme/provider (from' "$CODEXH/err" && grep -qF 'tessl exit 3' "$CODEXH/err"; then
    ok "a failed install with no reason line stops the review naming the plugin"
  else bad "unmatched failure output reached review or hid the plugin (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_source_pinned_dependency_stops_review() {
  run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/local":{"source":"file:../local"}}}'
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && ! grep -qF 'acme/local' "$CODEXH/tessl.log" && grep -qF "declares source 'file:../local'" "$CODEXH/err"; then
    ok "a source-pinned dependency stops the review before Codex"
  else bad "source-pinned dependency reached review (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_source_on_a_reviewer_owned_plugin_stops_review() {
  local name all_stopped=1
  for name in jbaruch/coding-policy jbaruch/nanoclaw-core; do
    run_with_marker $'tessl-workspace/tessl.json\n' "{\"dependencies\":{\"${name}\":{\"source\":\"file:../local\"}}}"
    if [[ $RC -eq 0 || -e "$CODEXH/codex.log" ]] || ! grep -qF "${name} (from jbaruch/repo-a:main/tessl-workspace/tessl.json): declares source 'file:../local'" "$CODEXH/err"; then
      all_stopped=0; echo "    not stopped: ${name}" >&2
    fi
    rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  done
  if [[ $all_stopped -eq 1 ]]; then ok "a source on coding-policy or nanoclaw-core stops the review before Codex"
  else bad "a source-pinned reviewer-owned plugin reached review"; fi
}

t_double_dot_in_a_name_is_a_valid_path() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local base="$CODEXH/base" rc=0
  mkdir -p "$base/.github" "$base/policy..v2" || exit 2
  printf 'policy..v2/tessl.json\n' > "$base/.github/fleet-review-manifests" || exit 2
  printf '{"dependencies":{"acme/provider":{"version":"1.0.0"}}}' > "$base/policy..v2/tessl.json" || exit 2
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_BASE_DIR="$base" \
    TESSL_LOG="$CODEXH/tessl.log" bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>"$CODEXH/err" || rc=$?
  if [[ $rc -eq 0 ]] && grep -qxF 'install --strict acme/provider@1.0.0' "$CODEXH/tessl.log"; then ok "a '..' inside a file name is a valid list entry"
  else bad "'..' inside a name rejected (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_latest_installs_strictly() {
  run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/provider":{"version":"latest"}}}'
  if [[ $RC -eq 0 ]] && grep -qxF 'install --strict acme/provider' "$CODEXH/tessl.log"; then ok "a latest dependency installs with --strict"
  else bad "latest dependency installed without --strict (rc=$RC, log: $(cat "$CODEXH/tessl.log"))"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_version_with_source_stops_review() {
  run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/local":{"version":"1.0.0","source":"file:../local"}}}'
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]] && ! grep -qF 'acme/local' "$CODEXH/tessl.log" && grep -qF "declares source 'file:../local'" "$CODEXH/err"; then
    ok "a dependency declaring both a version and a source stops before any registry install"
  else bad "version+source dependency installed from the registry or reached review (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_list_without_final_newline_still_installs() {
  run_with_marker 'tessl-workspace/tessl.json' '{"dependencies":{"acme/provider":{"version":"1.0.0"}}}'
  if [[ $RC -eq 0 ]] && grep -qxF 'install --strict acme/provider@1.0.0' "$CODEXH/tessl.log"; then ok "a final list entry without a newline is still installed"
  else bad "unterminated final list entry skipped (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_conflicting_pins_stop_review() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local base="$CODEXH/base" rc=0
  mkdir -p "$base/.github" "$base/tessl-workspace" || exit 2
  printf '{"dependencies":{"acme/provider":{"version":"1.0.0"}}}' > "$base/tessl.json" || exit 2
  printf 'tessl-workspace/tessl.json\n' > "$base/.github/fleet-review-manifests" || exit 2
  printf '{"dependencies":{"acme/provider":{"version":"2.0.0"}}}' > "$base/tessl-workspace/tessl.json" || exit 2
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_BASE_DIR="$base" \
    FAKE_BASE_MANIFEST="$base/tessl.json" TESSL_LOG="$CODEXH/tessl.log" CODEX_ARGV_LOG="$CODEXH/codex.log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>"$CODEXH/err" || rc=$?
  if [[ $rc -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF 'acme/provider@2.0.0' "$CODEXH/err" && grep -qF 'acme/provider@1.0.0' "$CODEXH/err" \
     && ! grep -qF 'acme/provider@2.0.0' "$CODEXH/tessl.log"; then ok "two manifests pinning one plugin differently stop the review before the second install"
  else bad "conflicting pins reached review or reinstalled (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_empty_extra_manifest_stops_review() {
  local env_line body; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local base="$CODEXH/base"
  mkdir -p "$base/.github" "$base/tessl-workspace" || exit 2
  printf 'tessl-workspace/tessl.json\n' > "$base/.github/fleet-review-manifests" || exit 2
  local all_stopped=1
  for body in '' $'  \n\t\n' '{"dependencies":false}' '{"dependencies":null}' 'null' \
              '{"dependencies":{"":{"version":"1.0.0"}}}' \
              '{"dependencies":{"jbaruch/coding-policy\njbaruch/nanoclaw-core":{"version":"1.0.0"}}}' \
              '{"dependencies":{"acme/provider":{"version":"1.0.0\nother/plugin"}}}' \
              '{"dependencies":{"acme/provider":{"version":"1.0.0","source":false}}}' \
              '{"dependencies":{"acme/provider":{"version":null}}}' \
              '{"dependencies":{"acme/provider":{"version":""}}}' \
              '{"dependencies":{"acme/provider":{}}}' \
              '{"dependencies":{"acme/provider":{"version":"1.0.0","pin":"other"}}}' \
              '{"dependencies":{"acme/provider":"1.0.0"}}'; do
    printf '%s' "$body" > "$base/tessl-workspace/tessl.json" || exit 2
    local rc=0
    rm -f "$CODEXH/codex.log"
    PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" FAKE_BASE_DIR="$base" \
      TESSL_LOG="$CODEXH/tessl.log" CODEX_ARGV_LOG="$CODEXH/codex.log" \
      bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>"$CODEXH/err" || rc=$?
    if [[ $rc -eq 0 || -e "$CODEXH/codex.log" ]] || ! grep -qF 'is not a JSON object' "$CODEXH/err"; then all_stopped=0; echo "    not stopped: $body" >&2; fi
  done
  if [[ $all_stopped -eq 1 ]]; then ok "an empty, null, non-object or malformed-record manifest stops before review"
  else bad "a malformed-shape listed manifest reached review"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_semver_with_prerelease_and_build_installs_exactly() {
  run_with_marker $'tessl-workspace/tessl.json\n' '{"dependencies":{"acme/provider":{"version":"1.2.3-rc.1+build.7"}}}'
  if [[ $RC -eq 0 ]] && grep -qxF 'install --strict acme/provider@1.2.3-rc.1+build.7' "$CODEXH/tessl.log"; then ok "a pin with both prerelease and build installs exactly"
  else bad "prerelease+build pin not installed exactly (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_refused_coding_policy_install_stops_review() {
  local env_line rc=0; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" TESSL_BLOCK=jbaruch/coding-policy \
    CODEX_ARGV_LOG="$CODEXH/codex.log" bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>"$CODEXH/err" || rc=$?
  if [[ $rc -ne 0 && ! -e "$CODEXH/codex.log" ]] && grep -qF 'tessl did not install jbaruch/coding-policy' "$CODEXH/err"; then
    ok "a refused coding-policy install stops the review before Codex"
  else bad "refused coding-policy install reached review (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_malformed_extra_manifest_stops_review() {
  run_with_marker $'tessl-workspace/tessl.json\n' '{not json'
  if [[ $RC -ne 0 && ! -e "$CODEXH/codex.log" ]]; then ok "a malformed extra manifest stops before review"
  else bad "malformed extra manifest reached review (rc=$RC)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "== fleet-review-one.sh tests =="
  t_happy
  t_model_pin
  t_nonzero_whitespace_still_posts_review
  t_bad_args
  t_missing_driver
  t_invalid_authoring_context
  t_invalid_source_sha
  t_missing_token
  t_core_context_from_trusted_base
  t_core_context_refusal_stops_review
  t_no_core_without_base_enrollment
  t_extra_manifest_installs_its_plugins
  t_extra_manifest_absent_on_base_stops_review
  t_latest_installs_strictly
  t_comment_only_marker_installs_only_policy
  t_escaping_marker_line_stops_review
  t_double_dot_in_a_name_is_a_valid_path
  t_policy_blocked_install_stops_review
  t_failed_install_without_reason_line_stops_review
  t_source_pinned_dependency_stops_review
  t_source_on_a_reviewer_owned_plugin_stops_review
  t_version_with_source_stops_review
  t_list_without_final_newline_still_installs
  t_conflicting_pins_stop_review
  t_empty_extra_manifest_stops_review
  t_refused_coding_policy_install_stops_review
  t_semver_with_prerelease_and_build_installs_exactly
  t_malformed_extra_manifest_stops_review
  echo "== summary: ${pass} passed, ${fail} failed =="
  [[ "$fail" -eq 0 ]]
fi
