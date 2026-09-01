#!/usr/bin/env bash
# Outcome-based tests for skills/teamlead/teamlead.sh.
#
# The launcher is copied into a temp skill dir per case, so the assertions are
# about the launcher's own behavior (interpreter probe, package probe, argument
# and PYTHONPATH forwarding) and never about the packaged module's logic. A fake
# interpreter records what it was handed (rules/testing-standards.md — fixtures
# built in setup, no network, no real Python module import).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Forwarding      -> `-m teamlead` plus every argument, verbatim.
#   2. PYTHONPATH      -> the skill dir is prefixed, a prior value preserved.
#   3. Missing package -> exit 1 naming the reinstall command.
#   4. Missing python  -> exit 1 naming the interpreter.
#   5. Exit passthrough-> the module's own status reaches the caller.
#
# Run: bash skills/teamlead/tests/test_teamlead_launcher.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake interpreter: records argv and PYTHONPATH, exits with $FAKE_PY_RC.
mk_fake_python() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake interpreter at $1"
#!/usr/bin/env bash
set -uo pipefail
printf 'ARGV: %s\n' "$*"
printf 'PYTHONPATH: %s\n' "${PYTHONPATH:-}"
exit "${FAKE_PY_RC:-0}"
FAKE
  chmod +x "$1" || die "could not chmod the fake interpreter at $1"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/teamlead.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "teamlead.sh not found/readable at $SCRIPT"

  TMP="$(mktemp -d -t teamlead-launcher-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  local skill="$TMP/skill"
  mkdir -p "$skill/teamlead" || die "could not create $skill/teamlead"
  cp "$SCRIPT" "$skill/teamlead.sh" || die "could not copy the launcher into $skill"
  local fakepy="$TMP/fakepy"
  mk_fake_python "$fakepy"

  FAIL=0; PASS=0

  # 1. Arguments reach the module unchanged, behind `-m teamlead`.
  local out rc
  out="$(env PY_BIN="$fakepy" bash "$skill/teamlead.sh" plan --roles developer,tester,reviewer 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -q -- "ARGV: -m teamlead plan --roles developer,tester,reviewer"; then
    pass; else fail "forwarding: expected the full argv behind -m teamlead, got rc=$rc out=$out"; fi

  # 2. The skill dir is prefixed onto PYTHONPATH, an existing value preserved.
  out="$(env PY_BIN="$fakepy" PYTHONPATH="/existing/path" bash "$skill/teamlead.sh" state 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -q "PYTHONPATH: ${skill}:/existing/path"; then
    pass; else fail "PYTHONPATH: expected the skill dir prefixed, got rc=$rc out=$out"; fi

  # 3. No packaged module -> refuse with the reinstall command.
  local bare="$TMP/bare"
  mkdir -p "$bare" || die "could not create $bare"
  cp "$SCRIPT" "$bare/teamlead.sh" || die "could not copy the launcher into $bare"
  out="$(env PY_BIN="$fakepy" bash "$bare/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "tessl install"; then
    pass; else fail "missing package: expected exit 1 naming the reinstall, got rc=$rc out=$out"; fi

  # 4. No interpreter -> refuse, naming what to install.
  out="$(env PY_BIN="$TMP/no-such-python" bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "no-such-python"; then
    pass; else fail "missing interpreter: expected exit 1 naming it, got rc=$rc out=$out"; fi

  # 5. The module's own exit status is the launcher's exit status.
  out="$(env PY_BIN="$fakepy" FAKE_PY_RC=7 bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 7 ]]; then
    pass; else fail "exit passthrough: expected 7, got rc=$rc out=$out"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
