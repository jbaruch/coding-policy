#!/usr/bin/env bash
# Tests for fleet-review-one.sh with local Git commits, the real manifest
# reader and poster, and stubbed GitHub, Tessl and Codex. No network.
#
# Run: bash .github/codex-review/tests/test_fleet_review_one.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/fleet-review-one.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: fleet-review-one.sh not readable at $SCRIPT" >&2; exit 2; }
REAL_GIT=$(command -v git) || exit 2
export REAL_GIT GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

# Best-effort cleanup that warns on failure rather than swallowing it
# (rules/error-handling.md — warn to stderr, never nothing).
rmwarn() { rm -rf "$@" || echo "test_fleet_review_one: warning: could not remove ${*}" >&2; }

# The posting identity fleet-review.yml supplies; the stub history reader
# below refuses any other login.
export REVIEWER_LOGIN="coding-policy-fleet-reviewer[bot]"

commit_fixture() {
  "$REAL_GIT" -C "$1" add -A &&
    "$REAL_GIT" -C "$1" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm Fixture --allow-empty
}

# Build a local remote and the toolchain for a single review fixture.
# Echoes: BIN CENTRAL CODEXH (space-separated) for the caller to consume.
# The function body is a `set -e` subshell so ANY failed mktemp/cat/chmod/mkdir
# during fixture setup aborts non-zero — the caller propagates it via
# `env_line=$(make_env) || exit 2` (rules/error-handling.md aggregate carve-out:
# a setup step that could corrupt the run carries its own failure check).
make_env() (
  set -e
  local bin central codexh remote
  bin=$(mktemp -d "${TMPDIR:-/tmp}/fleet-test-bin.XXXXXX")
  central=$(mktemp -d "${TMPDIR:-/tmp}/fleet-test-central.XXXXXX")
  codexh=$(mktemp -d "${TMPDIR:-/tmp}/fleet-test-context.XXXXXX")
  remote="$codexh/remote"
  "$REAL_GIT" init -qb main "$remote"
  commit_fixture "$remote"
  "$REAL_GIT" -C "$remote" checkout -qb candidate
  printf 'reviewed change\n' > "$remote/source.txt"
  commit_fixture "$remote"
  "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD
  "$REAL_GIT" -C "$remote" checkout -q main

  cat > "$bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == -c && "$3" == clone ]]; then
  args=("$@")
  url=${args[${#args[@]}-2]}
  [[ "$url" == https://github.com/jbaruch/repo-a.git ]] || exit 2
  : > "$CODEX_HOME/cloned"
  args[${#args[@]}-2]="$CODEX_HOME/remote"
  exec "$REAL_GIT" "${args[@]}"
fi
if [[ "$1" == -C && "$3" == diff && -n "${FAKE_DIFF_CHECK_RC:-}" ]]; then exit "$FAKE_DIFF_CHECK_RC"; fi
if [[ "$1" == -C && "${!#}" == refs/pull/7/head && -n "${MOVE_TRACKING_BASE:-}" ]]; then
  "$REAL_GIT" "$@"
  exec "$REAL_GIT" -C "$2" update-ref "refs/remotes/origin/$MOVE_TRACKING_BASE" FETCH_HEAD
fi
if [[ "$1" == -C && "$3" == rev-parse && -n "${FAKE_HEAD_SHA:-}" ]] &&
    [[ $(cat "$2/.git/FETCH_HEAD") == *refs/pull/7/head* ]]; then
  printf '%s\n' "$FAKE_HEAD_SHA"; exit 0
fi
if [[ "$1" == cat-file && "${3:-}" == "${DROP_BLOB:-absent}" ]]; then
  rm -- "$("$REAL_GIT" rev-parse --git-path "objects/${3:0:2}/${3:2}")"
fi
exec "$REAL_GIT" "$@"
EOF
  cat > "$bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  'api repos/jbaruch/repo-a/pulls/7')
    metadata='{"number":7,"base":{"ref":"main","repo":{"full_name":"jbaruch/repo-a"}}}'
    printf '%s\n' "${PR_METADATA-$metadata}" ;;
  'api --paginate repos/jbaruch/repo-a/pulls/7/reviews?per_page=100') echo '[]' ;;
  'api repos/jbaruch/repo-a/pulls/7/reviews --method POST --input -')
    payload=$(jq -c .)
    printf '%s\n' "$payload" >> "$CODEX_HOME/posted.jsonl"
    if [[ "${REJECT_APPROVAL:-0}" == 1 ]] && [[ $(jq -r .event <<< "$payload") == APPROVE ]]; then
      echo 'gh: approval refused (HTTP 422)' >&2
      exit 1
    fi ;;
  *) exit 2 ;;
esac
EOF
  cat > "$bin/tessl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
spec=${!#}
printf '%s\n' "$*" >> "$CODEX_HOME/installs"
case "${spec%@*}" in
  third-party/nonzero) echo 'installer-output-sentinel' >&2; exit 3 ;;
  third-party/refused) echo 'installer-output-sentinel'; exit 0 ;;
  jbaruch/coding-policy) if [[ -n "${POLICY_REFUSAL:-}" ]]; then exit "$POLICY_REFUSAL"; fi ;;
esac
mkdir -p ".tessl/plugins/${spec%@*}/rules"
EOF
  cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# Record argv one arg per line (so the test can assert the model pin), then
# write the canned structured result to the --output-last-message path.
[[ -z "${GH_TOKEN+x}" ]]
if [ -n "${CODEX_ARGV_LOG:-}" ]; then printf '%s\n' "$@" > "$CODEX_ARGV_LOG"; fi
if [ -n "${CODEX_PROMPT_LOG:-}" ]; then cat > "$CODEX_PROMPT_LOG"; else cat >/dev/null; fi
out=""
while [ $# -gt 0 ]; do [ "$1" = "--output-last-message" ] && out="${2:-}"; shift; done
report='{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy.","findings":[]}'
[ -n "$out" ] && printf '%s\n' "${CODEX_REPORT-$report}" > "$out"
if [ -n "${ADVANCE_HEAD:-}" ]; then "$REAL_GIT" -C "$CODEX_HOME/remote" update-ref refs/pull/7/head main; fi
exit 0
EOF
  chmod +x "$bin"/*

  # Fleet prompt/schema + the shared drivers all live under .github/codex-review.
  mkdir -p "$central/.github/codex-review"
  cp "$(dirname "$SCRIPT")/../reviewer_failure.py" "$central/.github/reviewer_failure.py"
  cp "$(dirname "$SCRIPT")/fleet-manifests.py" "$(dirname "$SCRIPT")/post-review.sh" \
    "$(dirname "$SCRIPT")/assert-no-secret-leak.sh" "$central/.github/codex-review/"
  echo '{}'     > "$central/.github/codex-review/fleet-schema.json"
  echo 'prompt' > "$central/.github/codex-review/fleet-prompt.md"
  cat > "$central/.github/codex-review/prior-feedback.py" <<'PY'
import sys
assert sys.argv[5:] == ["coding-policy-fleet-reviewer[bot]"], sys.argv
print('{"last_review":null,"items":[]}')
PY
  cat > "$central/.github/codex-review/policy-authoring-context.py" <<'PY'
import json
import sys
print(json.dumps({"eligible": False, "base": sys.argv[4], "head": sys.argv[6]}))
PY

  cat > "$central/.github/codex-review/materialize-core-authority.py" <<'PYTEST'
import os
from pathlib import Path
import sys
if os.environ.get("CORE_CONTEXT_LOG"):
    Path(os.environ["CORE_CONTEXT_LOG"]).write_text(str(Path.cwd()) + "\n" + str(Path(sys.argv[1]).resolve()))
raise SystemExit(int(os.environ.get("CORE_CONTEXT_RC", "0")))
PYTEST

  echo '{}' > "$codexh/auth.json"
  printf '%s %s %s' "$bin" "$central" "$codexh"
)

# --- happy path: routes to the poster with the right owner/repo/pr ---
t_happy() {
  local env_line; env_line=$(make_env) || exit 2   # propagate make_env setup failure (aggregate carve-out)
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  local out rc head posted
  head=$("$REAL_GIT" -C "$CODEXH/remote" rev-parse refs/pull/7/head) || exit 2
  out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" ADVANCE_HEAD=1 \
        bash "$SCRIPT" jbaruch repo-a 7 main 2>/dev/null); rc=$?
  posted=$(jq -r .commit_id "$CODEXH/posted.jsonl")
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 0 ]]                                        || { bad "happy: exit 0 (rc=$rc, out=$out)"; return; }
  [[ "$(jq -r .state <<<"$out")" == "posted" ]]         || { bad "happy: state posted ($out)"; return; }
  [[ "$posted" == "$head" && "$(jq -r .head_sha <<<"$out")" == "$head" ]] || { bad "happy: head advance retargeted the verdict ($out)"; return; }
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
    [[ $rc -eq 0 && "$(jq -r .state <<< "$out")" == posted && $saw_status -eq 0 ]] \
      || { bad "whitespace exit $check_rc must reach review with an explicit inspection result"; return; }
  done
  ok "nonzero whitespace and tool errors cannot strand the policy review"
}

# --- a failed history read or a malformed marker on the last review by the
# login stops the review before any verdict is posted ---
t_failed_history_posts_nothing() {
  local failing='exit 1' malformed
  malformed=$(cat <<'SH'
case "$*" in
  *graphql*) cat <<'JSON' ;;
{"data":{"node":{"body":"x\n\n<!-- coding-policy-blockers [1] -->","author":{"login":"coding-policy-fleet-reviewer"},"commit":{"oid":"1111111111111111111111111111111111111111"},"lastEditedAt":null,"editor":null}}}
JSON
  */reviews*) cat <<'JSON' ;;
[[{"user":{"login":"coding-policy-fleet-reviewer[bot]"},"body":"x\n\n<!-- coding-policy-blockers [1] -->",
   "created_at":"2026-11-17T00:00:00Z","commit_id":"1111111111111111111111111111111111111111","node_id":"PRR_1",
   "html_url":"https://github.com/jbaruch/repo-a/pull/7#pullrequestreview-1"}]]
JSON
  *) echo '[[]]' ;;
esac
SH
)
  local stub expect
  for stub in "$failing" "$malformed"; do
    local env_line; env_line=$(make_env) || exit 2
    read -r BIN CENTRAL CODEXH <<< "$env_line"
    cp "$(dirname "$SCRIPT")/prior-feedback.py" "$CENTRAL/.github/codex-review/prior-feedback.py" || exit 2
    sed -i.bak '/^  \*) exit 2 ;;/d; $d' "$BIN/gh" || exit 2
    printf '%s\n' '  *)' "$stub" '    ;;' 'esac' >> "$BIN/gh" || exit 2
    chmod +x "$BIN/gh" || exit 2
    expect="history unavailable"
    [[ "$stub" == "$malformed" ]] && expect="pullrequestreview-1 ends with a malformed blocker marker"
    local out err rc=0
    err="$CODEXH/err.log"
    out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
      bash "$SCRIPT" jbaruch repo-a 7 main 2>"$err") || rc=$?
    local diagnosed=1
    grep -qF "$expect" "$err" && diagnosed=0
    rmwarn "$BIN" "$CENTRAL" "$CODEXH"
    if [[ $rc -ne 0 && "$out" != *posted* && $diagnosed -eq 0 ]]; then
      ok "history failure ($expect) exits non-zero with a diagnostic and posts nothing"
    else
      bad "history failure ($expect) must not post (rc=$rc, out=$out)"
    fi
  done
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
  printf '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"latest"}}}' > "$CODEXH/remote/tessl.json" &&
    commit_fixture "$CODEXH/remote" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    CORE_CONTEXT_LOG="$CODEXH/core.log" \
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
  printf '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"^0.1.160"}}}' > "$CODEXH/remote/tessl.json" &&
    commit_fixture "$CODEXH/remote" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    CORE_CONTEXT_RC=2 CORE_CONTEXT_LOG="$CODEXH/core.log" CODEX_ARGV_LOG="$CODEXH/codex.log" \
    bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -ne 0 && -s "$CODEXH/core.log" && ! -e "$CODEXH/codex.log" && ! -e "$CODEXH/posted.jsonl" ]]; then
    ok "Core materializer refusal stops review and posting for a committed range"
  else
    bad "expected Core materializer refusal to stop review and posting for a committed range (rc=$rc)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_no_core_without_base_enrollment() {
  local env_line; env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  printf '{"dependencies":{}}' > "$CODEXH/remote/tessl.json" && commit_fixture "$CODEXH/remote" || exit 2
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    CORE_CONTEXT_LOG="$CODEXH/core.log" \
    CODEX_ARGV_LOG="$CODEXH/codex.log" bash "$SCRIPT" jbaruch repo-a 7 main >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 && ! -e "$CODEXH/core.log" && -s "$CODEXH/codex.log" ]]; then ok "no Core exception without trusted-base enrollment";
  else bad "Core exception selected outside trusted base (rc=$rc)"; fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

# Keep assertions on the invoked installs, exact diff and actual posted payload.
review_fixture() {
  local branch="$1"; shift
  rm -f "$CODEXH/installs" "$CODEXH/prompt" "$CODEXH/posted.jsonl" "$CODEXH/cloned" || exit 2
  RC=0
  OUT=$(env PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    CODEX_PROMPT_LOG="$CODEXH/prompt" "$@" bash "$SCRIPT" jbaruch repo-a 7 "$branch" 2>"$CODEXH/stderr") || RC=$?
}

t_root_only() {
  local env_line remote list
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  mkdir -p "$remote/.github" || exit 2
  printf '%s\n' '{"dependencies":{"jbaruch/provider":{"version":"latest"},"third-party/policy":{"version":"2.3.4"}}}' > "$remote/tessl.json" &&
    commit_fixture "$remote" || exit 2
  "$REAL_GIT" -C "$remote" checkout -qb head-only || exit 2
  printf '%s\n' 'head.json' > "$remote/.github/fleet-review-manifests" &&
    printf '%s\n' '{"dependencies":{"third-party/head-only":{"version":"1.0.0"}}}' > "$remote/head.json" &&
    printf '{}\0' > "$remote/tessl.json" &&
    commit_fixture "$remote" && "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
  "$REAL_GIT" -C "$remote" checkout -q main || exit 2
  for list in absent comments; do
    if [[ "$list" == comments ]]; then
      mkdir -p "$remote/.github" && printf '\n # root only\n' > "$remote/.github/fleet-review-manifests" &&
        commit_fixture "$remote" || exit 2
    fi
    review_fixture main MOVE_TRACKING_BASE=main
    if [[ "$RC" -eq 0 && $(cat "$CODEXH/installs") == \
      $'install --yes --accept-warnings jbaruch/coding-policy\ninstall --strict --yes --accept-warnings jbaruch/provider\ninstall --strict --yes third-party/policy@2.3.4' ]] &&
        jq -e '.event == "APPROVE" and (.body | contains("limitations") | not)' "$CODEXH/posted.jsonl" >/dev/null; then
      ok "$list base list keeps root installs despite a malformed head root and head-only list"
    else
      bad "$list base list must use root authority only (rc=$RC)"
    fi
  done
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_base_selection_and_repair() {
  local env_line remote base head metadata
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  mkdir -p "$remote/.github" "$remote/policy..v2" || exit 2
  printf '%s\n' '{"dependencies":{"third-party/shared":{"version":"1.2.3"},"third-party/conflict":{"version":"1.2.3"}}}' > "$remote/tessl.json" || exit 2
  printf 'repair/tessl.json\npolicy..v2/tessl.json' > "$remote/.github/fleet-review-manifests" || exit 2
  printf '%s\n' '{"dependencies":{"third-party/shared":{"version":"1.2.3"},"third-party/conflict":{"version":"2.0.0"},"third-party/base":{"version":"3.4.5-rc.2+build.7"}}}' \
    > "$remote/policy..v2/tessl.json" && commit_fixture "$remote" || exit 2
  "$REAL_GIT" -C "$remote" branch release/1.x || exit 2
  base=$("$REAL_GIT" -C "$remote" rev-parse release/1.x) || exit 2
  "$REAL_GIT" -C "$remote" checkout -qb repair || exit 2
  mkdir -p "$remote/repair" && printf '%s\n' '{"dependencies":{"third-party/repaired":{"version":"4.5.6"}}}' > "$remote/repair/tessl.json" || exit 2
  printf '%s\n' '{"dependencies":{"third-party/base":{"version":"9.9.9"}}}' > "$remote/policy..v2/tessl.json" &&
    commit_fixture "$remote" && "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
  head=$("$REAL_GIT" -C "$remote" rev-parse HEAD) || exit 2
  metadata='{"number":7,"base":{"ref":"release/1.x","repo":{"full_name":"jbaruch/repo-a"}}}'
  review_fixture release/1.x PR_METADATA="$metadata" MOVE_TRACKING_BASE=release/1.x
  if [[ "$RC" -eq 0 && $(cat "$CODEXH/installs") == \
    $'install --yes --accept-warnings jbaruch/coding-policy\ninstall --strict --yes third-party/shared@1.2.3\ninstall --strict --yes third-party/base@3.4.5-rc.2+build.7' ]] &&
      grep -qF "git diff $base...$head" "$CODEXH/prompt" &&
      grep -qF "\"base\": \"$base\", \"head\": \"$head\"" "$CODEXH/prompt" &&
      jq -e --arg head "$head" '.commit_id == $head and .event == "APPROVE" and
        (.body | contains("listed manifest is missing on the base") and contains("third-party/conflict: conflicting versions"))' \
        "$CODEXH/posted.jsonl" >/dev/null; then
    ok 'base pins dedupe, conflicts are excluded and the repair posts against the captured base and head'
  else
    bad "base-only pin selection or repair verdict was wrong (rc=$RC)"
  fi
  "$REAL_GIT" -C "$remote" branch -f release/1.x "$head" &&
    printf '%s\n' 'next source change' > "$remote/source.txt" && commit_fixture "$remote" &&
    "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
  review_fixture release/1.x PR_METADATA="$metadata"
  if [[ "$RC" -eq 0 ]] && grep -qFx 'install --strict --yes third-party/repaired@4.5.6' "$CODEXH/installs" &&
      jq -e '.event == "APPROVE" and (.body | contains("limitations") | not)' "$CODEXH/posted.jsonl" >/dev/null; then
    ok 'the next ordinary captured base installs the committed repair'
  else
    bad "next captured base must use the committed repair (rc=$RC)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_unusable_manifests() {
  local env_line remote
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  mkdir -p "$remote/.github" || exit 2
  printf 'empty.json\nnull.json\nfalse.json\nbad-id.json\nunsupported.json\nlink.json\n../path-input-sentinel\nnul\000path.json\n' \
    > "$remote/.github/fleet-review-manifests" || exit 2
  : > "$remote/empty.json" || exit 2
  printf '{"dependencies":null}' > "$remote/null.json" && printf '{"dependencies":false}' > "$remote/false.json" &&
    printf '%s' '{"dependencies":{"bad/\u0000id":{"version":"1.0.0"}}}' > "$remote/bad-id.json" || exit 2
  printf '%s' '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"1.0.0","source":"source-input-sentinel"},"jbaruch/optional":{"version":"latest"},"third-party/range":{"version":"pin-input-sentinel"}}}' \
    > "$remote/unsupported.json" && ln -s unsupported.json "$remote/link.json" && commit_fixture "$remote" || exit 2
  review_fixture main CORE_CONTEXT_LOG="$CODEXH/core.log"
  if [[ "$RC" -eq 0 && ! -e "$CODEXH/core.log" && $(wc -l < "$CODEXH/installs") -eq 2 ]] &&
      grep -qFx 'install --strict --yes --accept-warnings jbaruch/optional' "$CODEXH/installs" &&
      jq -e '.event == "APPROVE" and (.body | contains("line 6: use a regular committed file") and
        contains("line 7: use a repository-relative path") and contains("line 8: use a repository-relative path") and
        ([scan("declare one JSON object")] | length == 4) and
        contains("jbaruch/nanoclaw-core: declare a registry version") and
        (contains("jbaruch/optional") | not) and contains("third-party/range: declare an exact SemVer"))' \
        "$CODEXH/posted.jsonl" >/dev/null &&
      ! grep -q 'input-sentinel' "$CODEXH/stderr" "$CODEXH/prompt" "$CODEXH/posted.jsonl"; then
    ok 'first-party latest in extra manifest installs; malformed, nonregular and other unsupported inputs yield redacted limitations'
  else
    bad "optional input validation or redaction failed (rc=$RC)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_malformed_root_repair() {
  local env_line remote base head repair_head oid mode expected_error
  local limitation='The base tessl.json was unusable and was omitted for this repair review.'
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  mkdir -p "$remote/.github" &&
    printf '%s\n' 'extra.json' > "$remote/.github/fleet-review-manifests" &&
    printf '%s\n' '{"dependencies":{"third-party/base":{"version":"1.2.3"}}}' > "$remote/extra.json" &&
    printf '{}\0' > "$remote/tessl.json" && commit_fixture "$remote" || exit 2
  base=$("$REAL_GIT" -C "$remote" rev-parse main) || exit 2

  # The original head diverged before the root was added, so its review delta leaves the root unchanged.
  review_fixture main
  if [[ "$RC" -ne 0 && ! -e "$CODEXH/installs" && ! -e "$CODEXH/prompt" && ! -e "$CODEXH/posted.jsonl" ]] &&
      grep -qF 'Commit a valid replacement or delete tessl.json' "$CODEXH/stderr"; then
    ok 'a stale head with no root change in the review delta cannot repair a malformed base root'
  else
    bad "a stale head must not be treated as a root deletion (rc=$RC)"
  fi

  "$REAL_GIT" -C "$remote" checkout -qb root-repair || exit 2
  printf '%s\n' '{"dependencies":{"third-party/base":{"version":"9.9.9"}}}' > "$remote/extra.json" || exit 2
  for mode in replacement deletion; do
    case "$mode" in
      replacement)
        printf '%s\n' '{"dependencies":{"jbaruch/nanoclaw-core":{"version":"latest"},"third-party/head-root":{"version":"4.5.6"}}}' \
          > "$remote/tessl.json" || exit 2 ;;
      deletion) rm "$remote/tessl.json" || exit 2 ;;
    esac
    commit_fixture "$remote" && "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
    head=$("$REAL_GIT" -C "$remote" rev-parse HEAD) || exit 2
    if [[ "$mode" == replacement ]]; then repair_head=$head; fi
    review_fixture main MOVE_TRACKING_BASE=main CORE_CONTEXT_LOG="$CODEXH/core.log"
    if [[ "$RC" -eq 0 && ! -e "$CODEXH/core.log" && $(cat "$CODEXH/installs") == \
      $'install --yes --accept-warnings jbaruch/coding-policy\ninstall --strict --yes third-party/base@1.2.3' ]] &&
        grep -qF "git diff $base...$head" "$CODEXH/prompt" &&
        grep -qF "$limitation" "$CODEXH/prompt" &&
        jq -e --arg head "$head" --arg limitation "$limitation" '.commit_id == $head and .event == "APPROVE" and
          (.body | contains("\n\nReview installation limitations\n- " + $limitation + "\n"))' "$CODEXH/posted.jsonl" >/dev/null &&
        ! grep -qE 'third-party/head-root|jbaruch/nanoclaw-core|9\.9\.9' "$CODEXH/stderr" "$CODEXH/prompt" "$CODEXH/posted.jsonl"; then
      ok "root $mode reviews the captured head with only base extra authority and a fixed limitation"
    else
      bad "root $mode did not preserve base authority or post the repair limitation (rc=$RC)"
    fi
  done

  for mode in invalid nonregular unavailable; do
    case "$mode" in
      invalid)
        printf '%s\n' '{"dependencies":null}' > "$remote/tessl.json" && commit_fixture "$remote" || exit 2
        expected_error='Commit a valid replacement or delete tessl.json' ;;
      nonregular)
        rm "$remote/tessl.json" && ln -s extra.json "$remote/tessl.json" && commit_fixture "$remote" || exit 2
        expected_error='use a regular committed file' ;;
      unavailable)
        oid=$("$REAL_GIT" -C "$remote" rev-parse "$repair_head:tessl.json") &&
          "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head "$repair_head" || exit 2
        expected_error='git cat-file failed' ;;
    esac
    if [[ "$mode" == unavailable ]]; then
      review_fixture main DROP_BLOB="$oid"
    else
      "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
      review_fixture main
    fi
    if [[ "$RC" -ne 0 && ! -e "$CODEXH/installs" && ! -e "$CODEXH/prompt" && ! -e "$CODEXH/posted.jsonl" ]] &&
        grep -qF "$expected_error" "$CODEXH/stderr"; then
      ok "$mode head root fails before installs, Codex or posting"
    else
      bad "$mode head root must stop recovery (rc=$RC)"
    fi
  done
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_dispatch_and_checkout_trust() {
  local env_line remote metadata
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  review_fixture rejected-base-sentinel
  if [[ "$RC" -ne 0 && ! -e "$CODEXH/cloned" ]] && grep -qF 'did not confirm the dispatched PR and target' "$CODEXH/stderr" &&
      ! grep -qF 'rejected-base-sentinel' "$CODEXH/stderr"; then
    ok 'mismatched dispatch is redacted and refused before clone'
  else
    bad "mismatched target crossed the trust boundary (rc=$RC)"
  fi
  for metadata in '{' '{"number":8,"base":{"ref":"main","repo":{"full_name":"jbaruch/repo-a"}}}' \
    '{"number":7,"base":{"ref":"main","repo":{"full_name":"jbaruch/other"}}}'; do
    review_fixture main PR_METADATA="$metadata"
    if [[ "$RC" -ne 0 && ! -e "$CODEXH/cloned" ]] && grep -qF 'did not confirm the dispatched PR and target' "$CODEXH/stderr"; then
      ok 'invalid or mismatched GitHub PR metadata stops before clone'
    else
      bad "GitHub PR metadata was not validated (rc=$RC)"
    fi
  done
  "$REAL_GIT" -C "$remote" checkout -q candidate && ln -s missing "$remote/.tessl" &&
    commit_fixture "$remote" && "$REAL_GIT" -C "$remote" update-ref refs/pull/7/head HEAD || exit 2
  review_fixture main
  if [[ "$RC" -ne 0 && ! -e "$CODEXH/prompt" ]] && grep -qF 'checkout already contains .tessl' "$CODEXH/stderr"; then
    ok 'a committed dangling .tessl symlink stops before review'
  else
    bad "checkout can override the installed policy path (rc=$RC)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

t_install_refusal_and_posting() {
  local env_line remote mode event report
  env_line=$(make_env) || exit 2
  read -r BIN CENTRAL CODEXH <<< "$env_line"
  remote="$CODEXH/remote"
  printf '%s\n' '{"dependencies":{"third-party/nonzero":{"version":"1.0.0"},"third-party/refused":{"version":"1.0.0"}}}' \
    > "$remote/tessl.json" && commit_fixture "$remote" || exit 2
  for mode in pass fallback blocker; do
    case "$mode" in
      pass) review_fixture main; event=APPROVE ;;
      fallback) review_fixture main REJECT_APPROVAL=1; event=COMMENT ;;
      blocker)
        report='{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy.","findings":[{"path":"source.txt","line":1,"rule":"no-secrets","severity":"blocking","message":"[defect] Fixture finding independent of plugin availability."}]}'
        review_fixture main CODEX_REPORT="$report"; event=REQUEST_CHANGES ;;
    esac
    if [[ "$RC" -eq 0 && $(jq -r .event <<< "$OUT") == "$event" ]] &&
        jq -es --arg event "$event" 'map(.event) == (if $event == "COMMENT" then ["APPROVE","COMMENT"] else [$event] end)
          and all(.[]; .body | contains("Tessl did not install third-party/nonzero") and contains("Tessl did not install third-party/refused"))
          and (.[-1].body | contains("Fixture finding independent") == ($event == "REQUEST_CHANGES"))' "$CODEXH/posted.jsonl" >/dev/null &&
        grep -qF 'Tessl did not install third-party/refused' "$CODEXH/prompt" &&
        ! grep -qF 'installer-output-sentinel' "$CODEXH/stderr" "$CODEXH/prompt" "$CODEXH/posted.jsonl"; then
      ok "nonzero and exit-zero optional refusals reach $event with redacted limitations"
    else
      bad "optional failures must reach $event with limitations (rc=$RC)"
    fi
  done
  # Only the generated limitation contains this public fixture value.
  printf '%s\n' '{"fixture":"third-party/refused"}' > "$CODEXH/auth.json" || exit 2
  review_fixture main
  if [[ "$RC" -ne 0 && -s "$CODEXH/prompt" && ! -e "$CODEXH/posted.jsonl" ]] &&
      grep -qF 'review output contains credential material' "$CODEXH/stderr"; then
    ok 'the credential guard scans the amended summary before posting'
  else
    bad "the amended summary was not guarded (rc=$RC)"
  fi
  for mode in 0 3; do
    review_fixture main POLICY_REFUSAL="$mode"
    if [[ "$RC" -ne 0 && ! -e "$CODEXH/prompt" && ! -e "$CODEXH/posted.jsonl" ]] &&
        grep -qF 'did not install mandatory jbaruch/coding-policy' "$CODEXH/stderr"; then
      ok "mandatory policy refusal with Tessl exit $mode stops before review"
    else
      bad "mandatory policy refusal with Tessl exit $mode must stop (rc=$RC)"
    fi
  done
  local oid
  oid=$("$REAL_GIT" -C "$remote" rev-parse main:tessl.json) || exit 2
  review_fixture main DROP_BLOB="$oid"
  if [[ "$RC" -ne 0 && ! -e "$CODEXH/installs" ]] && grep -qF 'git cat-file failed' "$CODEXH/stderr"; then
    ok 'a missing Git blob object fails before installs instead of reading as an absent manifest'
  else
    bad "Git blob failure must stop the review (rc=$RC)"
  fi
  rmwarn "$BIN" "$CENTRAL" "$CODEXH"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "== fleet-review-one.sh tests =="
  t_happy
  t_model_pin
  t_nonzero_whitespace_still_posts_review
  t_failed_history_posts_nothing
  t_bad_args
  t_missing_driver
  t_invalid_authoring_context
  t_invalid_source_sha
  t_missing_token
  t_core_context_from_trusted_base
  t_core_context_refusal_stops_review
  t_no_core_without_base_enrollment
  t_root_only
  t_base_selection_and_repair
  t_unusable_manifests
  t_malformed_root_repair
  t_dispatch_and_checkout_trust
  t_install_refusal_and_posting
  echo "== summary: ${pass} passed, ${fail} failed =="
  [[ "$fail" -eq 0 ]]
fi
