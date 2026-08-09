#!/usr/bin/env bash
# Outcome-based tests for check-tessl-latest.sh. Drives the hook against fixture
# manifests via TESSL_LATEST_MANIFEST — no git repo needed, deterministic.
#
# Covers:
#   1. jbaruch/* pinned      -> warns, naming the pinned dep.
#   2. all jbaruch/* latest  -> silent.
#   3. only third-party pins  -> silent (jbaruch/* deps are latest).
#   4. no manifest            -> silent no-op.
#   5. malformed JSON         -> silent no-op, exit 0.
#
# Run: bash hooks/tests/test_check_tessl_latest.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-tessl-latest.sh"

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
run()  { OUT="$(TESSL_LATEST_MANIFEST="$1" bash "$SCRIPT" </dev/null 2>/dev/null)"; RC=$?; }

main() {
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: hook not found at $SCRIPT" >&2; exit 2; }
  command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  FAIL=0; PASS=0

  # 1. jbaruch/* pinned -> warns.
  local f="$TMP/1.json"
  printf '{"dependencies":{"jbaruch/coding-policy":{"version":"0.3.99"},"tessl/npm-react":{"version":"19.2.0"}}}\n' > "$f"
  run "$f"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("jbaruch/coding-policy")' >/dev/null 2>&1; then
    pass; else fail "pinned: expected warn, got RC=$RC OUT=$OUT"; fi

  # 2. all jbaruch/* latest -> silent.
  f="$TMP/2.json"
  printf '{"dependencies":{"jbaruch/coding-policy":{"version":"latest"}}}\n' > "$f"
  run "$f"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "all latest: expected silence, got RC=$RC OUT=$OUT"; fi

  # 3. only third-party pins -> silent (jbaruch/* are latest).
  f="$TMP/3.json"
  printf '{"dependencies":{"jbaruch/coding-policy":{"version":"latest"},"tessl/npm-react":{"version":"19.2.0"}}}\n' > "$f"
  run "$f"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "third-party only: expected silence, got RC=$RC OUT=$OUT"; fi

  # 4. no manifest -> silent no-op.
  run "$TMP/does-not-exist.json"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no manifest: expected silence, got RC=$RC OUT=$OUT"; fi

  # 5. malformed JSON -> silent no-op, exit 0.
  f="$TMP/5.json"; printf 'not json\n' > "$f"
  run "$f"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "malformed: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
