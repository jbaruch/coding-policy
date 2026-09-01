#!/usr/bin/env bash
# Outcome-based tests for skills/teamlead/wait-report.sh.
#
# Every case points HERDR_BIN at a fake binary this harness writes, replaying
# an agent state and a pane-probe verdict the case chooses (no live Herdr
# session, no network, no sleep of consequence — the poll interval is driven to
# 0 and the budget to a fixed number of seconds, so the suite is deterministic
# and fast; rules/testing-standards.md Determinism).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Marker + file   -> found true, exit 0, full envelope.
#   2. Marker, no file -> the file is half the signal: found false, exit 1.
#   3. File, no marker -> the marker is the other half: found false, exit 1.
#   4. Late marker     -> a second attempt finds it: found true, exit 0.
#   5. Blocked worker  -> exit 3 immediately, found false, state blocked.
#   6. agent get fails -> exit 2 (tool failure), never a "still working" read.
#   7. Probe fails     -> a non-timeout probe error is exit 2, not "not yet".
#   8. Usage error     -> exit 2 with a usage line.
#   9. Outside Herdr   -> exit 2, empty stdout.
#
# Run: bash skills/teamlead/tests/test_wait_report.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# The fake herdr. `agent get` replays $FAKE_STATUS/$FAKE_PANE (or fails when
# $FAKE_GET_ERR is set, or returns an unreadable payload when $FAKE_GET_BAD is).
# `pane wait-output` answers per $FAKE_MARKER: found | timeout | error |
# late (timeout on the first call, found afterwards, counted in $FAKE_COUNTER).
mk_fake_herdr() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake herdr at $1"
#!/usr/bin/env bash
set -uo pipefail
case "${1:-} ${2:-}" in
  "agent get")
    if [[ -n "${FAKE_GET_ERR:-}" ]]; then
      printf '{"error":{"code":"agent_not_found","message":"no such agent"}}\n' >&2
      exit 1
    fi
    if [[ -n "${FAKE_GET_BAD:-}" ]]; then
      printf '{"id":"cli:agent:get","result":{"type":"agent_info"}}\n'
      exit 0
    fi
    printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","pane_id":"%s","name":"%s"}}}\n' \
      "${FAKE_STATUS:-idle}" "${FAKE_PANE:-w2:p1}" "${3:-worker}"
    exit 0
    ;;
  "pane wait-output")
    case "${FAKE_MARKER:-timeout}" in
      found) printf '{"id":"cli:pane:wait-output","result":{"type":"pane_output_match"}}\n'; exit 0 ;;
      late)
        n=0
        [[ -r "${FAKE_COUNTER:?fake herdr: FAKE_COUNTER unset}" ]] && read -r n < "$FAKE_COUNTER"
        n=$((n + 1))
        printf '%s\n' "$n" > "$FAKE_COUNTER"
        if (( n >= 2 )); then
          printf '{"id":"cli:pane:wait-output","result":{"type":"pane_output_match"}}\n'
          exit 0
        fi
        printf '{"error":{"code":"timeout","message":"timed out waiting for output match"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
      error)
        printf '{"error":{"code":"pane_not_found","message":"no such pane"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
      *)
        printf '{"error":{"code":"timeout","message":"timed out waiting for output match"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
    esac
    ;;
esac
printf '{"error":{"code":"unsupported","message":"fake herdr: %s"}}\n' "$*" >&2
exit 2
FAKE
  chmod +x "$1" || die "could not chmod the fake herdr at $1"
}

# run <report-path> [extra env...] -> OUT, ERRTEXT, RC
run() {
  local report="$1"; shift
  RUN_SEQ=$((RUN_SEQ+1))
  local err="$TMP/stderr.$RUN_SEQ"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 \
    "$@" bash "$SCRIPT" worker "$report" </dev/null 2>"$err")"
  RC=$?
  ERRTEXT="$(cat "$err")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wait-report.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "wait-report.sh not found/readable at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"

  TMP="$(mktemp -d -t teamlead-wait-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  FAKE="$TMP/herdr"
  mk_fake_herdr "$FAKE"

  local report="$TMP/report.md" missing="$TMP/never-written.md"
  printf '# report\n' > "$report" || die "could not write $report"

  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. Both signals present -> found, exit 0, and the envelope carries every
  #    field the contract promises.
  run "$report" FAKE_MARKER=found FAKE_STATUS=done
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg p "$report" '
      (.agent == "worker") and (.found == true) and (.state == "done")
      and (.report_path == $p) and ((.elapsed_seconds | type) == "number")' >/dev/null 2>&1; then
    pass; else fail "marker+file: expected found true and a full envelope, got RC=$RC OUT=$OUT"; fi

  # 2. The marker without the file is not completion — a worker can print the
  #    line and still have written nothing.
  run "$missing" FAKE_MARKER=found FAKE_STATUS=idle
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "idle"' >/dev/null 2>&1; then
    pass; else fail "marker without file: expected found false + exit 1, got RC=$RC OUT=$OUT"; fi

  # 3. The file without the marker is not completion either — a half-written
  #    report from an earlier round looks identical on disk.
  run "$report" FAKE_MARKER=timeout FAKE_STATUS=working
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "working"' >/dev/null 2>&1; then
    pass; else fail "file without marker: expected found false + exit 1, got RC=$RC OUT=$OUT"; fi

  # 4. A marker that only shows up on a later attempt still completes the wait.
  local counter="$TMP/probe-count"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=60 \
    FAKE_MARKER=late FAKE_COUNTER="$counter" FAKE_STATUS=working \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/stderr.$RUN_SEQ")"; RC=$?
  local probes=0
  [[ -r "$counter" ]] && read -r probes < "$counter"
  if [[ $RC -eq 0 && "$probes" == "2" ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "late marker: expected found true after 2 probes, got RC=$RC probes=$probes OUT=$OUT"; fi

  # 5. A blocked worker is waiting on a human — return at once, do not spend
  #    the budget.
  run "$report" FAKE_MARKER=found FAKE_STATUS=blocked
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "blocked"' >/dev/null 2>&1; then
    pass; else fail "blocked: expected exit 3 with state blocked, got RC=$RC OUT=$OUT"; fi

  # 6. A failing `agent get` is a tool failure, never "still working".
  run "$report" FAKE_MARKER=found FAKE_GET_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "agent_not_found"; then
    pass; else fail "agent get failure: expected exit 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 6b. An unreadable `agent get` payload is a tool failure too.
  run "$report" FAKE_MARKER=found FAKE_GET_BAD=1
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "unreadable agent get: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 7. A non-timeout probe error must not read as "the marker is not there yet".
  run "$report" FAKE_MARKER=error
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "pane_not_found"; then
    pass; else fail "probe failure: expected exit 2 surfacing the code, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 8. Usage error -> exit 2 with a usage line.
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" bash "$SCRIPT" worker </dev/null 2>"$TMP/e8")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e8"; then
    pass; else fail "usage: expected exit 2 with a usage line, got RC=$RC OUT=$OUT"; fi

  # 9. Outside Herdr -> refuse, no stdout.
  OUT="$(env -u HERDR_ENV HERDR_BIN="$FAKE" bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e9")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "Herdr" "$TMP/e9"; then
    pass; else fail "outside Herdr: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
