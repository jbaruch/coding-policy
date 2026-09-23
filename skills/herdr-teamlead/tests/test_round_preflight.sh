#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/round-preflight.sh.
#
# The preflight composes existing owner scripts, so each case stubs those
# scripts in a shadow plugin directory rather than standing up Herdr, a GitHub
# token and a git worktree tree. What is under test is the AGGREGATION: which
# exit codes block a round, which cadence surfaces as due, and whether one
# failing check hides another's result.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Every check clean        -> ready, exit 0, nothing blocking.
#   2. HERDR_ENV unset          -> standalone, exit 1, no other check run.
#   3. Roster fails             -> blocks, names roster.sh and its code.
#   4. Authority fails          -> blocks; an unanswerable check is not permission.
#   5. Two checks fail          -> both reported; neither hides the other.
#   6. Capability due           -> surfaces in `due`, does NOT block.
#   7. Prune exit 1 vs 2        -> distinct statuses, both blocking.
#   8. --no-measure             -> headroom skipped, still ready.
#   9. Missing --repo/--checkout-> exit 2, usage error, no JSON verdict.
#  10. Every check's payload    -> carried through under `checks.<name>.detail`.
#  11. Gate pointers            -> resolved once, carried for the briefs.

set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the plugin dir"

stub() { # <dir> <name> <exit> <stdout>
  printf '#!/bin/sh\nprintf %s\nexit %s\n' "'$4'" "$3" > "$1/$2" || die "write stub $2"
  chmod +x "$1/$2" || die "chmod stub $2"
}

# A shadow plugin dir: the real preflight beside stubbed collaborators, so the
# script under test is the only unstubbed thing in it.
shadow() { # <dir> [roster-rc] [authority-rc] [prune-rc] [capability-due] [authorized]
  local dir="$1" roster="${2:-0}" authority="${3:-0}" prune="${4:-0}" due="${5:-false}" authorized="${6:-true}"
  mkdir -p "$dir" || die "mkdir $dir"
  cp "$REAL/round-preflight.sh" "$dir/" || die "copy the script under test"
  stub "$dir" roster.sh "$roster" '{"agents":[{"name":"grok"}]}'
  stub "$dir" verify-authority.sh "$authority" "{\"authorized\":${authorized}}"
  stub "$dir" prune-worktrees.sh "$prune" '{"removed":[],"kept":[]}'
  stub "$dir" resolve-gates.sh 0 '{"instructions":["AGENTS.md"],"workflows":[],"runners":[]}'
  printf '#!/bin/sh\ncase "$*" in\n  *capability-check*) printf %s; exit 0 ;;\n  *measure*) printf %s; exit 0 ;;\nesac\nexit 9\n' \
    "'{\"due\":$due,\"entries\":0}'" "'{\"agents\":{}}'" > "$dir/teamlead.sh" || die "write teamlead stub"
  chmod +x "$dir/teamlead.sh" || die "chmod teamlead stub"
}

run() { # <dir> [extra args...]
  local dir="$1"; shift
  OUT="$(HERDR_ENV=fixture bash "$dir/round-preflight.sh" \
    --repo owner/repo --checkout /tmp "$@" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

# <json> <python-expression-over-d>, so a case reads one field or counts a list.
field() { printf '%s' "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(eval(sys.argv[1])))' "$2"; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi; return 0; }

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/preflight-tests.XXXXXX")" || die "mktemp"
  trap cleanup EXIT
  ERRFILE="$TMP/err"

  echo "▶ the aggregate verdict" >&2

  shadow "$TMP/clean"
  run "$TMP/clean"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
     && [[ "$(field "$OUT" 'd["blocking"]')" == "[]" ]]; then
    pass; else fail "every check clean is ready, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/roster"
  OUT="$(HERDR_ENV='' bash "$TMP/roster/round-preflight.sh" --repo o/r --checkout /tmp 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["mode"]["status"]')" == '"standalone"' ]] \
     && ! printf '%s' "$OUT" | grep -q '"roster"'; then
    pass; else fail "an unset HERDR_ENV stops before any other check, got RC=$RC OUT=$OUT"; fi

  echo "▶ what blocks a round" >&2

  shadow "$TMP/badroster" 1
  run "$TMP/badroster"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'roster.sh exited 1'; then
    pass; else fail "a failing roster blocks and names its command, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/badauth" 0 2
  run "$TMP/badauth"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'not permission'; then
    pass; else fail "a failing authority check blocks, got RC=$RC OUT=$OUT"; fi

  # A denial is a verdict the verifier emits on exit 0. It blocks as surely as
  # a failed check: exit status alone never grants authority.
  shadow "$TMP/denied" 0 0 0 false false
  run "$TMP/denied"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'does not own' \
     && [[ "$(field "$OUT" 'd["checks"]["authority"]["status"]')" == '"denied"' ]]; then
    pass; else fail "an authority denial on exit 0 blocks, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/both" 1 2
  run "$TMP/both"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'len(d["blocking"])')" == "2" ]] \
     && printf '%s' "$OUT" | grep -q 'roster.sh exited 1' \
     && printf '%s' "$OUT" | grep -q 'verify-authority.sh exited 2'; then
    pass; else fail "one failing check must not hide another, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune1" 0 0 1
  run "$TMP/prune1"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"undecided"' ]]; then
    pass; else fail "prune exit 1 is undecided, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune2" 0 0 2
  run "$TMP/prune2"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]]; then
    pass; else fail "prune exit 2 is failed, got RC=$RC OUT=$OUT"; fi

  echo "▶ what is due without blocking" >&2

  shadow "$TMP/due" 0 0 0 true
  run "$TMP/due"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
     && printf '%s' "$OUT" | grep -q '"due": \["capability"\]'; then
    pass; else fail "a due cadence surfaces without blocking the round, got RC=$RC OUT=$OUT"; fi

  echo "▶ options and usage" >&2

  shadow "$TMP/nomeasure"
  run "$TMP/nomeasure" --no-measure
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"skipped"' ]]; then
    pass; else fail "--no-measure skips the one writing check, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/usage"
  for args in "--checkout /tmp" "--repo o/r"; do
    # shellcheck disable=SC2086  # deliberate word splitting of the fixture args
    OUT="$(HERDR_ENV=fixture bash "$TMP/usage/round-preflight.sh" $args 2>"$ERRFILE")"
    RC=$?
    ERRTEXT="$(cat "$ERRFILE")"
    if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'round-preflight:'; then
      pass; else fail "missing '$args' counterpart is a usage error, got RC=$RC OUT=$OUT"; fi
  done

  echo "▶ each check's own payload survives" >&2

  shadow "$TMP/detail"
  run "$TMP/detail"
  if [[ "$(field "$OUT" 'd["checks"]["authority"]["detail"]["authorized"]')" == "true" ]] \
     && [[ "$(field "$OUT" 'd["checks"]["capability"]["detail"]["entries"]')" == "0" ]]; then
    pass; else fail "a check's own payload must reach the caller, got OUT=$OUT"; fi

  # Resolved once here so five workers do not each spend turns finding the same
  # files; the lead renders it into the briefs' shared GATES value.
  if [[ "$(field "$OUT" 'd["checks"]["gates"]["detail"]["instructions"]')" == '["AGENTS.md"]' ]]; then
    pass; else fail "gate pointers must reach the caller for the briefs, got OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
