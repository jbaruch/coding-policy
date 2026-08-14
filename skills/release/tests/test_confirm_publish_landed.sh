#!/usr/bin/env bash
# Outcome-based tests for confirm-publish-landed.sh — the fail-SAFE gate.
#
# The critical property is fail-SAFE, not fail-open: the gate passes ONLY when
# the registry PROVES the artifact landed — the publish step's outcome is a
# hint, never proof, so the registry is ALWAYS read and compared, including on
# `success` (a green no-op / skipped / non-landing publish must not pass). The
# decision table under test:
#   registry advanced + outcome == success     -> pass (0), confirmed note
#   registry advanced + outcome != success      -> pass (0), ::warning:: (landed)
#   NOT advanced + outcome == success           -> fail (1), ::error:: (no-op)
#   NOT advanced + outcome != success           -> fail (1), ::error:: (failed)
#   registry read errors (cannot confirm)       -> fail (1), ::error::
#
# Approach: run the script as a subprocess with a `tessl` fake first on PATH
# (real jq reachable). confirm-publish-landed.sh calls registry-version.sh as a
# nested subprocess, which reads the fake — so MOCK_MODE, exported through the
# invocation, drives the registry state the gate sees. Exercises the real
# `set -euo pipefail` and exit codes end-to-end.
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

# Fake tessl. `current_0332` / `current_0331` report a specific latest version;
# `empty` reports a never-published tile; `error` fails the read (cannot
# confirm). MOCK_MODE selects; it is exported through invoke() so it reaches
# this fake even though confirm calls registry-version.sh (which calls tessl)
# one subprocess deeper.
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

# invoke <mock-mode> <baseline> <outcome>. Captures stdout (::warning:: and the
# success note land here) and stderr (::error:: lands here) separately.
invoke() {
  local mode="$1" baseline="$2" outcome="$3"
  OUT="$(MOCK_MODE="$mode" bash "$SCRIPT" jbaruch coding-policy "$baseline" "$outcome" 2>"$STUBDIR/err")"
  CODE=$?
  ERR="$(cat "$STUBDIR/err")"
}

echo "== confirm-publish-landed.sh tests =="

# --- outcome=success + registry advanced -> pass (confirmed) ---
invoke current_0332 "0.3.31" "success"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *"confirmed"* ]] && [[ "$OUT" != *"::warning::"* ]]; then
  pass "outcome=success + advanced -> exit 0, confirmed (no warning)"
else
  fail "success+advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# --- outcome=success but registry did NOT advance -> FAIL (green no-op / skip /
#     non-landing publish must not pass just because the step reported success) ---
invoke current_0331 "0.3.31" "success"
if [[ "$CODE" == 1 ]] && [[ "$ERR" == *"::error::"* ]] && [[ "$ERR" == *"did NOT advance"* ]]; then
  pass "outcome=success + NOT advanced -> exit 1 (green no-op rejected, fail-safe)"
else
  fail "success+not-advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# --- outcome=failure + registry advanced -> pass with ::warning:: (the
#     out-of-credits-after-publish case: landed despite a non-zero exit) ---
invoke current_0332 "0.3.31" "failure"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *"::warning::"* ]] && [[ "$OUT" == *"landed"* ]]; then
  pass "failure + registry advanced -> exit 0 with ::warning:: (landed)"
else
  fail "failure+advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# --- outcome=failure + NOT advanced -> FAIL with ::error:: (genuine failure) ---
invoke current_0331 "0.3.31" "failure"
if [[ "$CODE" == 1 ]] && [[ "$ERR" == *"::error::"* ]] && [[ "$ERR" == *"nothing landed"* ]]; then
  pass "failure + registry NOT advanced -> exit 1 with ::error:: (nothing landed)"
else
  fail "failure+not-advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# --- registry read errors -> cannot confirm -> FAIL (fail-safe) ---
invoke error "0.3.31" "failure"
if [[ "$CODE" == 1 ]] && [[ "$ERR" == *"::error::"* ]] && [[ "$ERR" == *"cannot read the registry"* ]]; then
  pass "registry read error -> exit 1 (cannot confirm, fail-safe)"
else
  fail "registry-read-error: code=$CODE out='$OUT' err='$ERR'"
fi

# --- a non-success, non-failure outcome (cancelled) still reconciles ---
invoke current_0332 "0.3.31" "cancelled"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *"::warning::"* ]]; then
  pass "outcome=cancelled + advanced -> exit 0 with ::warning::"
else
  fail "cancelled+advanced: code=$CODE out='$OUT' err='$ERR'"
fi

# --- first-ever publish: empty baseline, current version present -> advance ---
invoke current_0332 "" "failure"
if [[ "$CODE" == 0 ]] && [[ "$OUT" == *"::warning::"* ]] && [[ "$OUT" == *"landed"* ]]; then
  pass "first publish (empty baseline) + version present -> exit 0 (landed)"
else
  fail "first-publish: code=$CODE out='$OUT' err='$ERR'"
fi

# --- failure + registry still empty (never published) -> FAIL (nothing landed) ---
invoke empty "" "failure"
if [[ "$CODE" == 1 ]] && [[ "$ERR" == *"nothing landed"* ]]; then
  pass "failure + registry still empty -> exit 1 (nothing landed, fail-safe)"
else
  fail "failure+empty-registry: code=$CODE out='$OUT' err='$ERR'"
fi

# --- wrong arity is a usage error (exit 2), not a silent gate verdict ---
OUT="$(bash "$SCRIPT" jbaruch coding-policy "0.3.31" 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage"
else
  fail "arity: code=$CODE out='$OUT' err='$ERR'"
fi

echo ""
echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
