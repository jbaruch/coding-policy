#!/usr/bin/env bash
# Outcome-based tests for hooks/bounded-run.sh.
#
# Covers:
#   1. Pass-through   -> stdin, stdout and the exit code are the command's own.
#   2. Budget spent   -> exit 124 with a diagnostic, and a grandchild the
#                        command started is stopped with it.
#   3. Not startable  -> exit 125 with a diagnostic.
#   4. Usage          -> a non-positive budget is exit 125.
#
# The harness drops `set -e` to aggregate results (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Run: bash hooks/tests/test_bounded_run.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${HERE}/../bounded-run.sh"
PASS=0
FAIL=0
TMP=""

die() { echo "fatal: $*" >&2; exit 2; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() {
  if [[ -n "$TMP" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi
  return 0
}

main() {
  command -v python3 >/dev/null || die "python3 is required"
  TMP="$(mktemp -d)" || die "mktemp failed"
  trap cleanup EXIT
  local out rc

  echo "1. stdin, stdout and the exit code pass through"
  rc=0
  out="$(printf 'hello\n' | bash "$RUNNER" 5 bash -c 'cat; exit 7' 2>"$TMP/1.err")" || rc=$?
  if [[ $rc -eq 7 && "$out" == hello && ! -s "$TMP/1.err" ]]; then pass
  else fail "pass-through: rc=$rc out=$out err=$(cat "$TMP/1.err")"; fi

  echo "2. a spent budget is exit 124, and the command's children stop with it"
  rc=0
  # The grandchild records its pid, then outlives the budget unless stopped.
  bash "$RUNNER" 1 bash -c 'sleep 60 & echo $! > "$0"; wait' "$TMP/grandchild.pid" 2>"$TMP/2.err" || rc=$?
  local grandchild alive=1
  grandchild="$(cat "$TMP/grandchild.pid")" || die "the grandchild never recorded its pid"
  if ! kill -0 "$grandchild" 2>"$TMP/kill.err"; then alive=0; fi
  if [[ $rc -eq 124 && $alive -eq 0 ]] && grep -q "budget" "$TMP/2.err"; then pass
  else
    fail "budget: rc=$rc grandchild alive=$alive err=$(cat "$TMP/2.err")"
    if (( alive )); then kill "$grandchild" || echo "warn: could not stop $grandchild" >&2; fi
  fi

  echo "3. a command that cannot start is exit 125 with a diagnostic"
  rc=0
  bash "$RUNNER" 5 "$TMP/no-such-command" 2>"$TMP/3.err" || rc=$?
  if [[ $rc -eq 125 ]] && grep -q "cannot start" "$TMP/3.err"; then pass
  else fail "not startable: rc=$rc err=$(cat "$TMP/3.err")"; fi

  echo "4. a budget that is not a positive integer is a usage error"
  rc=0
  bash "$RUNNER" 0 true 2>"$TMP/4.err" || rc=$?
  if [[ $rc -eq 125 ]] && grep -q "usage" "$TMP/4.err"; then pass
  else fail "usage: rc=$rc err=$(cat "$TMP/4.err")"; fi

  echo
  echo "passed=${PASS} failed=${FAIL}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
