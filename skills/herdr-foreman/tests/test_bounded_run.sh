#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/bounded-run.sh.
#
# No case waits on a clock. Every budget is an hour, far past any case; the
# expiry case ends it with SIGALRM, the runner's own documented trigger, and
# every handshake is a FIFO read that returns when the other side acts.
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
# Run: bash skills/herdr-foreman/tests/test_bounded_run.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${HERE}/../bounded-run.sh"
PASS=0
FAIL=0
TMP=""

#: A budget no case comes near; expiry is always signalled, never waited for.
HOUR=3600

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
  out="$(printf 'hello\n' | bash "$RUNNER" "$HOUR" bash -c 'cat; exit 7' 2>"$TMP/1.err")" || rc=$?
  if [[ $rc -eq 7 && "$out" == hello && ! -s "$TMP/1.err" ]]; then pass
  else fail "pass-through: rc=$rc out=$out err=$(cat "$TMP/1.err")"; fi

  echo "2. an ended budget is exit 124, and the command's children stop with it"
  # held: the command opens its write end before anything else, and the
  # grandchild inherits it, so a reader sees end-of-file exactly when both
  # are gone. started: the command writes its grandchild's pid after that
  # open, so the budget is never ended before the FIFO has its writers.
  mkfifo "$TMP/started" "$TMP/held" || die "mkfifo failed"
  cat "$TMP/held" > "$TMP/held.out" &
  local reader=$!
  bash "$RUNNER" "$HOUR" bash -c 'exec 3>"$0"; sleep 3600 & echo $! > "$1"; wait' "$TMP/held" "$TMP/started" 2>"$TMP/2.err" &
  local runner=$! grandchild
  read -r grandchild < "$TMP/started" || die "the command never reported its grandchild"
  kill -ALRM "$runner" || die "cannot signal the runner $runner"
  rc=0
  wait "$runner" || rc=$?
  # Returns only once every writer of `held` is gone: the grandchild included.
  wait "$reader" || die "the FIFO reader failed"
  if [[ $rc -eq 124 && -n "$grandchild" ]] && grep -q "budget" "$TMP/2.err"; then pass
  else fail "budget: rc=$rc grandchild=$grandchild err=$(cat "$TMP/2.err")"; fi

  echo "3. a command that cannot start is exit 125 with a diagnostic"
  rc=0
  bash "$RUNNER" "$HOUR" "$TMP/no-such-command" 2>"$TMP/3.err" || rc=$?
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
