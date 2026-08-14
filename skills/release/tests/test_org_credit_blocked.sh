#!/usr/bin/env bash
# Outcome-based tests for org-credit-blocked.sh.
#
# The contract: read `tessl org usage --org <org> --json` and emit
# {"blocked":true} or {"blocked":false} on stdout, distinguishing a clean
# boolean verdict (exit 0) from a tool/parse failure (exit 2, empty stdout,
# diagnostic on stderr). The load-bearing property is fail-SAFE shape handling:
# a missing or non-boolean `.credits.blocked`, a non-JSON body, or a tessl
# failure must exit 2 — NEVER emit a fabricated {"blocked":false}, which would
# fail-open the confirm gate (a garbled body read as "not blocked" would
# preserve red for a real credit outage).
#
# Approach: run the script as a subprocess with a `tessl` fake first on PATH
# (real jq stays reachable), so the real `set -euo pipefail`, the EXIT trap,
# and the exit codes are exercised end-to-end. The fake selects its fixture via
# MOCK_CREDIT, exported through the invocation.
#
# Run: bash skills/release/tests/test_org_credit_blocked.sh
# Exit 0 on all-pass; 1 with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/org-credit-blocked.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: org-credit-blocked.sh not readable at $SCRIPT" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

STUBDIR="$(mktemp -d)" || { echo "fatal: mktemp -d failed — cannot stage the tessl stub; check TMPDIR" >&2; exit 2; }
[[ -n "$STUBDIR" && -d "$STUBDIR" ]] || { echo "fatal: mktemp -d returned no usable directory (got '${STUBDIR}')" >&2; exit 2; }
cleanup_stubdir() {
  if [[ -n "${STUBDIR:-}" ]]; then
    if ! rm -rf "$STUBDIR"; then
      echo "warning: could not remove stub dir ${STUBDIR} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_stubdir EXIT
export PATH="$STUBDIR:$PATH"

# Fake tessl. Handles `tessl org usage --org <o> --json`; selects a fixture via
# MOCK_CREDIT. The `blocked`/`unblocked` fixtures mirror the real payload shape
# ({"plan":...,"credits":{"state":...,"blocked":<bool>,...}}).
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  org)
    [[ "$2" == "usage" ]] || { echo "stub tessl: unsupported org subcommand: $2" >&2; exit 2; }
    case "${MOCK_CREDIT:-}" in
      blocked)
        printf '{"plan":{"name":"team"},"credits":{"state":"over_budget","blocked":true,"remaining":-9930,"overLimit":true}}\n' ;;
      unblocked)
        printf '{"plan":{"name":"team"},"credits":{"state":"ok","blocked":false,"remaining":1000,"overLimit":false}}\n' ;;
      non_json)
        printf '<html>502 Bad Gateway</html>\n' ;;
      tessl_fails)
        echo "error: not authenticated" >&2
        exit 1 ;;
      missing_blocked)
        printf '{"plan":{"name":"team"},"credits":{"state":"ok","remaining":1000}}\n' ;;
      non_bool_blocked)
        printf '{"plan":{"name":"team"},"credits":{"state":"ok","blocked":"yes"}}\n' ;;
      *) echo "stub tessl: unknown MOCK_CREDIT='${MOCK_CREDIT:-}'" >&2; exit 99 ;;
    esac
    ;;
  *) echo "stub tessl: unsupported invocation: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "$STUBDIR/tessl"

# Invoke with MOCK_CREDIT exported so it reaches the fake tessl subprocess.
invoke() {
  local mode="$1"
  OUT="$(MOCK_CREDIT="$mode" bash "$SCRIPT" jbaruch 2>"$STUBDIR/err")"
  CODE=$?
  ERR="$(cat "$STUBDIR/err")"
}

echo "== org-credit-blocked.sh tests =="

# --- blocked org -> {"blocked":true}, exit 0 ---
invoke blocked
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"blocked":true}' ]]; then
  pass "over_budget/blocked=true -> {\"blocked\":true}, exit 0"
else
  fail "blocked: code=$CODE out='$OUT' err='$ERR'"
fi

# --- unblocked org -> {"blocked":false}, exit 0 ---
invoke unblocked
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"blocked":false}' ]]; then
  pass "ok/blocked=false -> {\"blocked\":false}, exit 0"
else
  fail "unblocked: code=$CODE out='$OUT' err='$ERR'"
fi

# --- non-JSON body -> exit 2, empty stdout, NO fabricated verdict ---
invoke non_json
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not valid JSON"* ]]; then
  pass "non-JSON body -> exit 2, empty stdout, actionable diagnostic"
else
  fail "non-JSON: code=$CODE out='$OUT' err='$ERR'"
fi

# --- tessl itself failing -> exit 2 with the tool's stderr surfaced ---
invoke tessl_fails
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not authenticated"* ]]; then
  pass "tessl failure -> exit 2, tool stderr surfaced"
else
  fail "tessl failure: code=$CODE out='$OUT' err='$ERR'"
fi

# --- valid JSON but .credits.blocked absent -> exit 2 (malformed shape is a
#     tool failure, NOT a fabricated {"blocked":false}) ---
invoke missing_blocked
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"no boolean .credits.blocked"* ]]; then
  pass "missing .credits.blocked -> exit 2 (not a fabricated not-blocked verdict)"
else
  fail "missing-blocked: code=$CODE out='$OUT' err='$ERR'"
fi

# --- .credits.blocked present but not a boolean ("yes") -> exit 2 ---
invoke non_bool_blocked
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"no boolean .credits.blocked"* ]]; then
  pass "non-boolean .credits.blocked -> exit 2"
else
  fail "non-bool-blocked: code=$CODE out='$OUT' err='$ERR'"
fi

# --- wrong arity is a usage error, not a silent default ---
OUT="$(bash "$SCRIPT" 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage"
else
  fail "arity: code=$CODE out='$OUT' err='$ERR'"
fi

echo ""
echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
