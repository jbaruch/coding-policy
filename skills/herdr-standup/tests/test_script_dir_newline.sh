#!/usr/bin/env bash
# Outcome tests: the standup scripts still reach the sibling herdr-foreman
# skill when their own directory's name ends in a newline (#592).
#
# A `$(dirname ...)` capture strips that newline, so the script looked for
# ../herdr-foreman beside a directory that does not exist. Each case stages one
# script under such a directory, with a stub herdr-foreman sibling that records
# being run, and asserts the record.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. standup-wait.sh -> runs ../herdr-foreman/wait-report.sh.
#   2. standup-ask.sh  -> runs ../herdr-foreman/foreman.sh.
#
# Run: bash skills/herdr-standup/tests/test_script_dir_newline.sh
set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi; return 0; }

NL=$'\n'

# A stub sibling: records its name in $MARKER, prints <stdout>, exits <rc>.
stub() { # <path> <rc> <stdout>
  printf '%s' "$3" > "$1.out" || die "write the stub output for $1"
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    # shellcheck disable=SC2016  # The format string is the stub's source: ${MARKER:?} expands in the stub.
    printf 'printf "%%s\\n" %q >> "${MARKER:?}"\n' "${1##*/}"
    printf 'cat -- %q\n' "$1.out"
    printf 'exit %d\n' "$2"
  } > "$1" || die "write the stub $1"
}

# A plugin tree whose herdr-standup directory name ends in a newline, holding
# the script, beside a herdr-foreman directory for the stubs.
stage() { # <case> <script> -> STAGE, FOREMAN_DIR
  STAGE="$TMP/$1/herdr-standup${NL}"
  FOREMAN_DIR="$TMP/$1/herdr-foreman"
  mkdir -p "$STAGE" "$FOREMAN_DIR" || die "create the staging directories for $1"
  cp "$REAL/$2" "$STAGE/$2" || die "copy $2 into the staging directory"
}

reached() { # <sibling-name>
  [[ -f "$MARKER" ]] && grep -qxF "$1" "$MARKER"
}

main() {
  REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the herdr-standup skill dir"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/standup-dir-newline.XXXXXX")" || die "mktemp failed"
  trap cleanup EXIT
  MARKER="$TMP/marker"

  if ! mkdir "$TMP/probe${NL}" 2>"$TMP/probe.err"; then
    echo "skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/probe.err"))" >&2
    exit 0
  fi

  local report="$TMP/reports/worker.md" out rc err

  # 1.
  stage wait standup-wait.sh
  stub "$FOREMAN_DIR/wait-report.sh" 0 '{"found":true}'
  : > "$MARKER" || die "reset $MARKER"
  out="$(MARKER="$MARKER" bash "$STAGE/standup-wait.sh" worker "$report" 2>"$TMP/err")"; rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 0 && "$out" == '{"found":true}' ]] && reached wait-report.sh; then
    pass; else fail "standup-wait.sh: expected wait-report.sh run, got rc=$rc out=$out err=$err"; fi

  # 2. An idle worker is measured through foreman.sh before anything is sent;
  #    the stub's failure stops the standup there.
  stage ask standup-ask.sh
  stub "$FOREMAN_DIR/foreman.sh" 3 ''
  stub "$TMP/herdr" 0 '{"result":{"agent":{"agent_status":"idle","pane_id":"w1:p1"}}}'
  chmod +x "$TMP/herdr" || die "chmod the herdr stub"
  : > "$MARKER" || die "reset $MARKER"
  out="$(MARKER="$MARKER" HERDR_ENV=1 HERDR_BIN="$TMP/herdr" STANDUP_REPORT_PATH_MAX_COLS=1000 \
    bash "$STAGE/standup-ask.sh" worker "$report" 2>"$TMP/err")"; rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 2 && "$err" == *"marker-fit"* ]] && reached foreman.sh; then
    pass; else fail "standup-ask.sh: expected foreman.sh run, got rc=$rc out=$out err=$err"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
