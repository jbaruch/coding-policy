#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/wait-report.sh.
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
#  10. Relative path   -> refused up front (exit 2), never probed.
#  11. Probe argv      -> options first, PANE_ID last, matched with --match.
#  12. No pane id     -> a payload without one is exit 2, named once.
#  13. No set -u abort -> every case's stderr is checked for "unbound
#                         variable", including the success path, which emits
#                         its JSON and exits 0 before the abort would fire.
#  14. Soft-wrapped     -> the prefix and the basename on different rows still
#                         complete: the long line wraps in `--source visible`.
#  14b. Wrap in basename-> a row break inside the basename is exit 4, never found.
#  15. Decoy REPORT     -> another report's line does NOT complete this wait,
#                         including one whose basename ENDS with this one's.
#  16. Confirm read     -> a failing `pane read` is a tool failure (exit 2).
#  17. Blocked flicker  -> one `blocked` read with no dialog keeps waiting.
#  18. Blocked twice    -> two `blocked` reads plus a dialog row is exit 3.
#  19. Blocked, no UI   -> two `blocked` reads without a dialog keep waiting.
#  20. Metachar decoy   -> `.` in the name is literal; `reportXmd` never confirms.
#  21. Unconfirmed idle -> file present + idle twice + no marker = exit 4.
#  21b. Single idle read-> one idle read is not exit 4; the budget ends it.
#  21c. Bad idle-reads  -> a non-integer override is exit 2, not an abort.
#  21d. Zero as `00`    -> refused too; the count is compared in base 10.
#
# Run: bash skills/herdr-teamlead/tests/test_wait_report.sh
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
    if [[ -n "${FAKE_GET_NO_PANE:-}" ]]; then
      printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","name":"%s"}}}\n' \
        "${FAKE_STATUS:-idle}" "${3:-worker}"
      exit 0
    fi
    status="${FAKE_STATUS:-idle}"
    # A status that changes after the first read, for the flicker cases.
    if [[ -n "${FAKE_STATUS_AFTER:-}" && -n "${FAKE_GET_COUNTER:-}" ]]; then
      n=0
      [[ -r "$FAKE_GET_COUNTER" ]] && read -r n < "$FAKE_GET_COUNTER"
      n=$((n + 1))
      printf '%s\n' "$n" > "$FAKE_GET_COUNTER"
      (( n >= 2 )) && status="$FAKE_STATUS_AFTER"
    fi
    printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","pane_id":"%s","name":"%s"}}}\n' \
      "$status" "${FAKE_PANE:-w2:p1}" "${3:-worker}"
    exit 0
    ;;
  "pane read")
    printf '%s\n' "${FAKE_PANE_TEXT-REPORT: /tmp/report.md}"
    exit "${FAKE_PANE_READ_RC:-0}"
    ;;
  "pane wait-output")
    [[ -n "${FAKE_ARGV_FILE:-}" ]] && printf '%s\n' "$*" >> "$FAKE_ARGV_FILE"
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
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_PANE_TEXT="REPORT: ${report}" \
    "$@" bash "$SCRIPT" worker "$report" </dev/null 2>"$err")"
  RC=$?
  ERRTEXT="$(cat "$err")"
  assert_no_unbound "$ERRTEXT" "run #${RUN_SEQ}"
}

# A `set -u` abort fires mid-flight, after output has already been emitted, so
# an exit code and a stdout payload can both look correct while the script died
# on its way out. Every case checks for it (a live run hit exactly that: the
# success JSON, exit 0, then `pane: unbound variable`).
assert_no_unbound() { # <stderr-text> <label>
  case "$1" in
    *"unbound variable"*)
      fail "$2: stderr carries a set -u abort: $1" ;;
    *) pass ;;
  esac
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
  local base=""

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
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=60 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=late FAKE_COUNTER="$counter" FAKE_STATUS=working \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/stderr.$RUN_SEQ")"; RC=$?
  local probes=0
  [[ -r "$counter" ]] && read -r probes < "$counter"
  if [[ $RC -eq 0 && "$probes" == "2" ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "late marker: expected found true after 2 probes, got RC=$RC probes=$probes OUT=$OUT"; fi

  # 5. A blocked worker is waiting on a human — return at once, do not spend
  #    the budget.
  run "$report" FAKE_MARKER=found FAKE_STATUS=blocked \
    FAKE_PANE_TEXT="Do you want to allow this edit?"
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

  # 10. A relative report path resolves against the caller's cwd, so it is
  #     refused before any probe rather than silently answering about the
  #     wrong file.
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" FAKE_MARKER=found \
    bash "$SCRIPT" worker "reports/worker.md" </dev/null 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "relative" "$TMP/e10"; then
    pass; else fail "relative path: expected exit 2 naming it, got RC=$RC OUT=$OUT"; fi

  # 10b. The refusal names the absolute form to pass instead. The literal
  #      searched for is `$PWD/`, so the pattern stays single-quoted
  #      (shellcheck SC2016 does not apply: no expansion is wanted here).
  # shellcheck disable=SC2016
  if grep -q '\$PWD/' "$TMP/e10"; then
    pass; else fail "relative path: expected an actionable message, got $(cat "$TMP/e10")"; fi

  # 11. herdr's usage line is `pane wait-output [OPTIONS] <--match|--regex>
  #     <PANE_ID>`; the probe must match it, and use --match for a literal.
  local argvfile="$TMP/probe-argv"
  RUN_SEQ=$((RUN_SEQ+1))
  env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_ARGV_FILE="$argvfile" FAKE_PANE="w9:p9" \
    bash "$SCRIPT" worker "$report" </dev/null >/dev/null 2>"$TMP/stderr.$RUN_SEQ"
  local probe_argv=""
  [[ -r "$argvfile" ]] && read -r probe_argv < "$argvfile"
  if [[ "$probe_argv" == *"--match REPORT: "* && "$probe_argv" == *" w9:p9" ]]; then
    pass; else fail "probe argv: expected options first and the pane id last, got '$probe_argv'"; fi
  if [[ "$probe_argv" != *"--regex"* ]]; then
    pass; else fail "probe argv: a literal marker must use --match, got '$probe_argv'"; fi

  # 12. An `agent get` payload with no pane id would otherwise probe the
  #     literal pane "unknown" on every attempt and report a generic herdr
  #     error; name the real cause once instead.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_GET_NO_PANE=1 \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e12")"; RC=$?
  ERRTEXT="$(cat "$TMP/e12")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "reported no pane id"; then
    pass; else fail "no pane id: expected exit 2 naming it, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  assert_no_unbound "$ERRTEXT" "no pane id"

  # 13. The success path specifically: stdout is the envelope and stderr is
  #     EMPTY. A `set -u` abort here would arrive after the JSON, where an
  #     exit-code check alone would call the run clean.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e13")"; RC=$?
  if [[ $RC -eq 0 && ! -s "$TMP/e13" ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "clean success: expected exit 0 with empty stderr, got RC=$RC ERR=$(cat "$TMP/e13")"; fi

  # 14. The real pane shape this has to read: Claude Code and Grok soft-wrap
  #     the long REPORT line, so the prefix lands on one row and the path
  #     continues on the next. A full-path literal would never match; the
  #     basename on the wrapped row is what confirms it.
  RUN_SEQ=$((RUN_SEQ+1))
  local wrapped
  base="$(basename "$report")"
  wrapped="REPORT: /Users/jbaruch/.worktrees/round-3/reports/
${base}"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="$wrapped" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e14")"; RC=$?
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "wrapped marker: expected found true, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e14")"; fi
  assert_no_unbound "$(cat "$TMP/e14")" "wrapped marker"

  # 14b. The wrap a per-row check cannot see: the row break lands INSIDE the
  #      basename (`reports/12-` / `developer-fix.md`, observed live on a Codex
  #      pane with a 170-character report path). This is NOT a confirmation --
  #      no text-only join can prove the second row continues the first -- so
  #      with the file present and the worker idle the wait must end in exit 4
  #      and hand the pane to the lead, not sit on the budget and not complete.
  RUN_SEQ=$((RUN_SEQ+1))
  local split_inside
  split_inside="  REPORT: /Users/jbaruch/.worktrees/round-3/reports/${base:0:3}
  ${base:3}"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="$split_inside" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e14b")"; RC=$?
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false and (.reason | test("marker unconfirmed"))' >/dev/null 2>&1; then
    pass; else fail "wrap inside basename: expected exit 4 with a reason, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e14b")"; fi
  assert_no_unbound "$(cat "$TMP/e14b")" "wrap inside basename"

  # 15. A `REPORT: ` line for a DIFFERENT report — the previous round's, or
  #     another worker's — must not complete this wait. The report file exists
  #     the whole time, so the pane text is the only thing separating them.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    FAKE_PANE_TEXT="REPORT: /tmp/other-round/developer-notes.md" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e15")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "decoy marker: expected found false, got RC=$RC OUT=$OUT"; fi

  # 15b. The decoy that a substring test gets wrong: another worker's
  #      `reviewer-report.md` CONTAINS this worker's `report.md`. The basename
  #      has to match as a whole path component, not as a suffix.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    FAKE_PANE_TEXT="REPORT: /tmp/other-round/reviewer-${base}" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e15b")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "suffix decoy: expected found false, got RC=$RC OUT=$OUT"; fi

  # 16. The confirming read failing is a tool failure, not "not yet" — the
  #     marker was seen and the answer is unknown, which must not read as a
  #     worker still working.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_READ_RC=1 FAKE_PANE_TEXT="" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e16")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "could not be confirmed" "$TMP/e16"; then
    pass; else fail "confirm read failure: expected exit 2, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e16")"; fi

  # 17. herdr flickered `blocked` for a single read on a Codex pane running in
  #     Full Access, where a permission prompt resolves itself before anything
  #     can see it — the script reported a dialog that was never on screen,
  #     with elapsed_seconds 0. One `blocked` read is not a blocked worker.
  RUN_SEQ=$((RUN_SEQ+1))
  local counter17="$TMP/get-count-17"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_STATUS_AFTER=working \
    FAKE_GET_COUNTER="$counter17" FAKE_PANE_TEXT="thinking…" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e17")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1 \
     && grep -q "flicker" "$TMP/e17"; then
    pass; else fail "blocked flicker: expected the wait to continue, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e17")"; fi

  # 18. Two `blocked` reads AND a dialog row on the pane is the real thing.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked \
    FAKE_PANE_TEXT="  1. Yes  2. No
  Press enter to continue" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e18")"; RC=$?
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '.state == "blocked" and .found == false' >/dev/null 2>&1; then
    pass; else fail "blocked confirmed: expected exit 3, got RC=$RC OUT=$OUT"; fi

  # 18b. The refusal points at the operator, never at the lead answering it.
  if grep -q "let them answer it" "$TMP/e18"; then
    pass; else fail "blocked confirmed: expected the operator-answers message, got $(cat "$TMP/e18")"; fi

  # 19. Two `blocked` reads with nothing on screen is still not a dialog: a
  #     pane with no marker keeps the wait alive rather than ending the round.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_PANE_TEXT="⠧ working on it" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e19")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "blocked without a dialog: expected the wait to continue, got RC=$RC OUT=$OUT"; fi

  # 20. A regex metacharacter in the name is literal: `report.md` must not
  #     confirm on `reportXmd`. The quoted part of a bash `=~` pattern is
  #     matched as a string; this pins it in case a refactor unquotes it.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="  REPORT: /Users/jbaruch/.worktrees/round-3/reports/${base//./X}" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e20")"; RC=$?
  if [[ $RC -ne 0 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "metachar decoy: expected found false, got RC=$RC OUT=$OUT"; fi

  # 21. The file is there, the worker reads idle twice, and the marker is never
  #     seen at all (wait-output times out): exit 4 with a reason, well inside
  #     the budget, instead of an hour of silence.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21")"; RC=$?
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false and (.reason | test("marker unconfirmed"))' >/dev/null 2>&1 \
     && grep -q "still unconfirmed after 2 consecutive reads" "$TMP/e21"; then
    pass; else fail "unconfirmed idle: expected exit 4 with a reason, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21")"; fi
  assert_no_unbound "$(cat "$TMP/e21")" "unconfirmed idle"

  # 21b. One idle read with the file present is NOT exit 4: the counter needs
  #      two in a row. With a zero budget the wait ends on exit 1, no reason.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21b")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and (has("reason") | not)' >/dev/null 2>&1; then
    pass; else fail "single idle read: expected exit 1 (budget) with no reason, got RC=$RC OUT=$OUT"; fi

  # 21c. A bad TEAMLEAD_UNCONFIRMED_IDLE_READS is exit 2 with a named cause,
  #      never an arithmetic abort with no JSON.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_UNCONFIRMED_IDLE_READS=soon \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21c")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "TEAMLEAD_UNCONFIRMED_IDLE_READS must be a positive integer" "$TMP/e21c"; then
    pass; else fail "bad idle-reads override: expected exit 2 naming it, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21c")"; fi
  # 21d. `00` is zero, not a positive count: refused the same way.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_UNCONFIRMED_IDLE_READS=00 \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21d")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "must be a positive integer" "$TMP/e21d"; then
    pass; else fail "zero-with-leading-zero override: expected exit 2, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21d")"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
