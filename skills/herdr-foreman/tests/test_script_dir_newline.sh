#!/usr/bin/env bash
# Outcome tests: a herdr-foreman script still reaches what sits beside it when
# its own directory's name ends in a newline (#592).
#
# A `$(dirname ...)` capture strips that newline, so the script looked beside
# a directory that does not exist. Each case stages one script under such a
# directory with stand-ins that record being reached, and asserts the record.
# wait-report.sh, round-preflight.sh and the classify scripts carry the same
# case in their own suites, beside the fixtures those paths need.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. foreman.sh            -> puts its own directory on PYTHONPATH.
#   2. start-judge-worker.sh -> runs the foreman.sh beside it.
#   3. sweep-worktrees.sh    -> imports foreman.prune_result from beside it.
#
# Run: bash skills/herdr-foreman/tests/test_script_dir_newline.sh
set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi; return 0; }

NL=$'\n'

# A fresh staging directory whose name ends in a newline, holding the script.
stage() { # <case> <script> -> STAGE
  STAGE="$TMP/$1/herdr-foreman${NL}"
  mkdir -p "$STAGE" || die "create the staging directory for $1"
  cp "$REAL/$2" "$STAGE/$2" || die "copy $2 into the staging directory"
}

main() {
  REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the herdr-foreman skill dir"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/foreman-dir-newline.XXXXXX")" || die "mktemp failed"
  trap cleanup EXIT
  local marker="$TMP/marker" out rc err

  if ! mkdir "$TMP/probe${NL}" 2>"$TMP/probe.err"; then
    echo "skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/probe.err"))" >&2
    exit 0
  fi

  # 1. A stand-in interpreter answers the version probe, then records the
  #    PYTHONPATH it was handed.
  stage launcher foreman.sh
  mkdir -p "$STAGE/foreman" || die "create the foreman package directory"
  cat > "$TMP/fakepy" <<'FAKE' || die "write the stand-in interpreter"
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then printf '3.12\n'; exit 0; fi
printf '%s' "$PYTHONPATH" > "${MARKER:?}"
FAKE
  chmod +x "$TMP/fakepy" || die "chmod the stand-in interpreter"
  rm -f "$marker" || die "reset $marker"
  out="$(env -u PYTHONPATH MARKER="$marker" PY_BIN="$TMP/fakepy" bash "$STAGE/foreman.sh" state 2>"$TMP/err")"; rc=$?
  err="$(cat "$TMP/err")"
  # The launcher hands over `pwd`'s spelling of the directory, and so does
  # this; both carry a sentinel across the newline strip.
  local want got
  want="$(cd -- "$STAGE" && pwd && printf x)" || die "enter $STAGE"
  want="${want%$'\n'x}"
  got="$(if [[ -f "$marker" ]]; then cat -- "$marker"; fi; printf x)" || die "read $marker"
  got="${got%x}"
  if [[ $rc -eq 0 && "$got" == "$want" && "$got" == *"$NL" ]]; then
    pass; else fail "foreman.sh: expected PYTHONPATH to be its own directory, got rc=$rc out=$out err=$err"; fi

  # 2.
  stage judge start-judge-worker.sh
  cat > "$STAGE/foreman.sh" <<'STUB' || die "write the foreman.sh stub"
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" > "${MARKER:?}"
STUB
  rm -f "$marker" || die "reset $marker"
  out="$(MARKER="$marker" bash "$STAGE/start-judge-worker.sh" "$TMP/plan.json" w1:p1 2>"$TMP/err")"; rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 0 && -f "$marker" && "$(cat "$marker")" == start-judge ]]; then
    pass; else fail "start-judge-worker.sh: expected foreman.sh start-judge run, got rc=$rc out=$out err=$err"; fi

  # 3. The sweep imports the shared result-shape module from beside itself
  #    before it looks at any repository; an empty root needs nothing else.
  stage sweep sweep-worktrees.sh
  mkdir -p "$STAGE/foreman" "$TMP/sweep-root" || die "create the sweep fixtures"
  : > "$STAGE/foreman/__init__.py" || die "write the package marker"
  cat > "$STAGE/foreman/prune_result.py" <<'PY' || die "write the prune_result stand-in"
import os

with open(os.environ["MARKER"], "w", encoding="utf-8") as handle:
    handle.write("imported")


def prune_schema_error(_payload):
    return None
PY
  rm -f "$marker" || die "reset $marker"
  out="$(MARKER="$marker" bash "$STAGE/sweep-worktrees.sh" "$TMP/sweep-root" 2>"$TMP/err")"; rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 0 && -f "$marker" && "$(cat "$marker")" == imported ]]; then
    pass; else fail "sweep-worktrees.sh: expected foreman.prune_result imported from beside it, got rc=$rc out=$out err=$err"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
