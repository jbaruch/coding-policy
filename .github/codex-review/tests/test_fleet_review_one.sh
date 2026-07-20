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

# Build a fake toolchain (git/tessl/codex) + stubbed CENTRAL_DIR driver scripts.
# Echoes: BIN CENTRAL CODEXH (space-separated) for the caller to consume.
make_env() {
  local bin central codexh
  bin=$(mktemp -d); central=$(mktemp -d); codexh=$(mktemp -d)

  cat > "$bin/git" <<'EOF'
#!/usr/bin/env bash
# clone <flags..> <url> <dest>: create an empty checkout at dest; everything else is a no-op.
if [ "${1:-}" = "clone" ]; then dest="${!#}"; mkdir -p "$dest/.git"; fi
exit 0
EOF
  printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/tessl"
  cat > "$bin/codex" <<'EOF'
#!/usr/bin/env bash
# Write the canned structured result to the --output-last-message path.
out=""
while [ $# -gt 0 ]; do [ "$1" = "--output-last-message" ] && out="${2:-}"; shift; done
[ -n "$out" ] && printf '{"summary":"Policy loaded: 21 rule files from jbaruch/coding-policy.","verdict":"pass","findings":[]}' > "$out"
exit 0
EOF
  chmod +x "$bin"/*

  mkdir -p "$central/.github/codex-review"
  echo '{}'     > "$central/.github/codex-review/schema.json"
  echo 'prompt' > "$central/.github/codex-review/prompt.md"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$central/.github/codex-review/assert-no-secret-leak.sh"
  cat > "$central/.github/codex-review/post-review.sh" <<'EOF'
#!/usr/bin/env bash
# args: <owner> <repo> <pr> <result-json> — echo them back so the test can assert routing.
printf '{"state":"posted","owner":"%s","repo":"%s","pr":"%s"}\n' "$1" "$2" "$3"
EOF

  echo '{"tokens":{"access_token":"x"}}' > "$codexh/auth.json"
  printf '%s %s %s' "$bin" "$central" "$codexh"
}

# --- happy path: routes to the poster with the right owner/repo/pr ---
t_happy() {
  read -r BIN CENTRAL CODEXH < <(make_env)
  local out rc
  out=$(PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
        bash "$SCRIPT" jbaruch repo-a 7 main aaa111 2>/dev/null); rc=$?
  rm -rf "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 0 ]]                                        || { bad "happy: exit 0 (rc=$rc, out=$out)"; return; }
  [[ "$(jq -r .state <<<"$out")" == "posted" ]]         || { bad "happy: state posted ($out)"; return; }
  [[ "$(jq -r .owner <<<"$out")" == "jbaruch" ]]        || { bad "happy: owner routed ($out)"; return; }
  [[ "$(jq -r .repo  <<<"$out")" == "repo-a" ]]         || { bad "happy: repo routed ($out)"; return; }
  [[ "$(jq -r .pr    <<<"$out")" == "7" ]]              || { bad "happy: pr routed ($out)"; return; }
  ok "happy path routes to the poster with owner/repo/pr"
}

# --- wrong arg count -> exit 2 ---
t_bad_args() {
  local rc=0; bash "$SCRIPT" only three args >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 2 ]] && ok "wrong arg count -> exit 2" || bad "bad_args: expected exit 2 (rc=$rc)"
}

# --- a missing CENTRAL_DIR driver file -> exit 1 ---
t_missing_driver() {
  read -r BIN CENTRAL CODEXH < <(make_env)
  rm -f "$CENTRAL/.github/codex-review/schema.json"
  local rc=0
  PATH="$BIN:$PATH" GH_TOKEN=tok CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    bash "$SCRIPT" jbaruch repo-a 7 main aaa111 >/dev/null 2>&1 || rc=$?
  rm -rf "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -eq 1 ]] && ok "missing driver file -> exit 1" || bad "missing_driver: expected exit 1 (rc=$rc)"
}

# --- missing GH_TOKEN -> non-zero (unset :? guard) ---
t_missing_token() {
  read -r BIN CENTRAL CODEXH < <(make_env)
  local rc=0
  PATH="$BIN:$PATH" CENTRAL_DIR="$CENTRAL" CODEX_HOME="$CODEXH" \
    bash "$SCRIPT" jbaruch repo-a 7 main aaa111 >/dev/null 2>&1 || rc=$?
  rm -rf "$BIN" "$CENTRAL" "$CODEXH"
  [[ $rc -ne 0 ]] && ok "missing GH_TOKEN -> non-zero" || bad "missing_token: expected non-zero"
}

echo "== fleet-review-one.sh tests =="
t_happy
t_bad_args
t_missing_driver
t_missing_token
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
