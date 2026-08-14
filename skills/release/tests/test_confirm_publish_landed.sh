#!/usr/bin/env bash
# Outcome-based tests for confirm-publish-landed.sh — the fail-SAFE gate.
#
# The critical property is fail-SAFE, not fail-open. The gate passes ONLY when
# the registry PROVES the artifact landed AND — for a non-zero publish exit —
# the publish step's own output carried the out-of-credits SIGNATURE (passed in
# as the <credit-signature> argument by the smart-publish step). The publish
# step's outcome is a hint, never proof, so the registry is ALWAYS read and
# compared, including on `success`. The decision table under test (6 rows):
#   success  + advanced                       -> pass (0), credit_signature null
#   success  + NOT advanced                   -> fail (1), credit_signature null
#   non-succ + advanced + signature=true      -> pass (0), credit_signature true
#   non-succ + advanced + signature=false     -> fail (1), credit_signature false
#   non-succ + NOT advanced                   -> fail (1), credit_signature null
#   registry read error (cannot confirm)      -> fail (1), credit_signature null
#
# The script emits ONE JSON object on stdout (gate/landed/current/baseline/
# credit_signature/reason) and NO ::warning::/::error:: annotations — those move
# to the composite action. So assertions read the JSON fields on stdout; the
# exit code still reflects the gate (0 pass, 1 fail, 2 usage).
#
# Approach: run the script as a subprocess with a `tessl` fake first on PATH
# (real jq reachable). confirm-publish-landed.sh calls registry-version.sh
# (which calls `tessl api`) to read the registry; MOCK_MODE drives that state.
# The credit-signature is a plain argument now, so no credit-state fake is
# needed.
#
# Run: bash skills/release/tests/test_confirm_publish_landed.sh
# Exit 0 on all-pass; 1 with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/confirm-publish-landed.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: confirm-publish-landed.sh not readable at $SCRIPT" >&2; exit 2; }

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

# Fake tessl. Answers `tessl api <ws>/<tile>/versions` — registry state, driven
# by MOCK_MODE. current_0332 / current_0331 report a specific latest version;
# empty is a never-published tile; error fails the read (cannot confirm).
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  api)
    case "${MOCK_MODE:-}" in
      current_0332) printf '{"data":[{"attributes":{"version":"0.3.32"}}]}\n' ;;
      current_0331) printf '{"data":[{"attributes":{"version":"0.3.31"}}]}\n' ;;
      empty)        printf '{"data":[]}\n' ;;
      error)        echo "error: registry unreachable" >&2; exit 1 ;;
      *) echo "stub tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
    esac
    ;;
  *) echo "stub tessl: unsupported invocation: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "$STUBDIR/tessl"

# invoke <mock-mode> <baseline> <outcome> <credit-signature>. Captures stdout
# (the JSON verdict) and stderr separately.
invoke() {
  local mode="$1" baseline="$2" outcome="$3" sig="${4:-}"
  OUT="$(MOCK_MODE="$mode" bash "$SCRIPT" jbaruch coding-policy "$baseline" "$outcome" "$sig" 2>"$STUBDIR/err")"
  CODE=$?
  ERR="$(cat "$STUBDIR/err")"
}

echo "== confirm-publish-landed.sh tests =="

# Row 1 --- success + advanced -> pass (confirmed), signature ignored ---
invoke current_0332 "0.3.31" "success" ""
if [[ "$CODE" == 0 ]] \
  && [[ "$OUT" == *'"gate":"pass"'* ]] \
  && [[ "$OUT" == *'"landed":true'* ]] \
  && [[ "$OUT" == *'"credit_signature":null'* ]] \
  && [[ "$OUT" == *"confirmed"* ]]; then
  pass "success + advanced -> gate pass, credit_signature null"
else
  fail "success+advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# Row 2 --- success but registry did NOT advance -> FAIL (green no-op rejected) ---
invoke current_0331 "0.3.31" "success" ""
if [[ "$CODE" == 1 ]] \
  && [[ "$OUT" == *'"gate":"fail"'* ]] \
  && [[ "$OUT" == *'"landed":false'* ]] \
  && [[ "$OUT" == *'"credit_signature":null'* ]] \
  && [[ "$OUT" == *"did NOT advance"* ]]; then
  pass "success + NOT advanced -> gate fail (green no-op rejected, fail-safe)"
else
  fail "success+not-advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# Row 3 --- non-success + advanced + signature=true -> PASS (out-of-credits
#           after publish: landed despite a non-zero exit) ---
invoke current_0332 "0.3.31" "failure" "true"
if [[ "$CODE" == 0 ]] \
  && [[ "$OUT" == *'"gate":"pass"'* ]] \
  && [[ "$OUT" == *'"landed":true'* ]] \
  && [[ "$OUT" == *'"credit_signature":true'* ]] \
  && [[ "$OUT" == *"out-of-credits signature"* ]]; then
  pass "non-success + advanced + signature=true -> gate pass (tolerated credit outage)"
else
  fail "failure+advanced+sig-true: code=$CODE out='$OUT' err='$ERR'"
fi

# Row 4 --- non-success + advanced + signature=false -> FAIL (a non-credit
#           post-publish failure that also landed the artifact must stay red) ---
invoke current_0332 "0.3.31" "failure" "false"
if [[ "$CODE" == 1 ]] \
  && [[ "$OUT" == *'"gate":"fail"'* ]] \
  && [[ "$OUT" == *'"landed":true'* ]] \
  && [[ "$OUT" == *'"credit_signature":false'* ]] \
  && [[ "$OUT" == *"non-credit post-publish failure"* ]]; then
  pass "non-success + advanced + signature=false -> gate fail (non-credit failure preserved red)"
else
  fail "failure+advanced+sig-false: code=$CODE out='$OUT' err='$ERR'"
fi

# Row 5 --- non-success + NOT advanced -> FAIL (nothing landed; signature
#           ignored because nothing shipped) ---
invoke current_0331 "0.3.31" "failure" "true"
if [[ "$CODE" == 1 ]] \
  && [[ "$OUT" == *'"gate":"fail"'* ]] \
  && [[ "$OUT" == *'"landed":false'* ]] \
  && [[ "$OUT" == *'"credit_signature":null'* ]] \
  && [[ "$OUT" == *"nothing landed"* ]]; then
  pass "non-success + NOT advanced -> gate fail (nothing landed, signature ignored)"
else
  fail "failure+not-advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# Row 6 --- registry read error -> cannot confirm -> FAIL ---
invoke error "0.3.31" "failure" "true"
if [[ "$CODE" == 1 ]] \
  && [[ "$OUT" == *'"gate":"fail"'* ]] \
  && [[ "$OUT" == *'"credit_signature":null'* ]] \
  && [[ "$OUT" == *"cannot read the registry"* ]]; then
  pass "registry read error -> gate fail (cannot confirm, fail-safe)"
else
  fail "registry-read-error: code=$CODE out='$OUT' err='$ERR'"
fi

# --- a non-success, non-failure outcome (cancelled) still reconciles: advanced
#     + signature=true -> pass ---
invoke current_0332 "0.3.31" "cancelled" "true"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *'"gate":"pass"'* ]] && [[ "$OUT" == *'"credit_signature":true'* ]]; then
  pass "outcome=cancelled + advanced + signature=true -> gate pass"
else
  fail "cancelled+advanced+sig-true: code=$CODE out='$OUT' err='$ERR'"
fi

# --- first-ever publish: empty baseline, current present, signature=true ->
#     advance, gate pass ---
invoke current_0332 "" "failure" "true"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *'"gate":"pass"'* ]] && [[ "$OUT" == *'"landed":true'* ]]; then
  pass "first publish (empty baseline) + version present + signature=true -> gate pass (landed)"
else
  fail "first-publish: code=$CODE out='$OUT' err='$ERR'"
fi

# --- failure + registry still empty (never published) -> FAIL (nothing landed;
#     signature ignored) ---
invoke empty "" "failure" "true"
if [[ "$CODE" == 1 ]] && [[ "$OUT" == *'"gate":"fail"'* ]] && [[ "$OUT" == *"nothing landed"* ]]; then
  pass "failure + registry still empty -> gate fail (nothing landed, fail-safe)"
else
  fail "failure+empty-registry: code=$CODE out='$OUT' err='$ERR'"
fi

# --- wrong arity is a usage error (exit 2, empty stdout), not a silent verdict ---
OUT="$(bash "$SCRIPT" jbaruch coding-policy "0.3.31" "failure" 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage, no JSON"
else
  fail "arity: code=$CODE out='$OUT' err='$ERR'"
fi

echo ""
echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
