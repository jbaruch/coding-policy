#!/usr/bin/env bash
# The two wrappers that keep release rc-dispatch out of a Markdown example.
#
# Each exists because the logic it owns has a failure mode a caller retyping it
# gets wrong: an empty baseline passes the registry-advance conjunct vacuously,
# and collapsing rc 2 into rc 1 reports an unreachable tool as a failed publish
# (jbaruch/coding-policy#450).
#
# `set -e` is dropped so every check runs and the suite reports an aggregate;
# each check captures its own status (rules/error-handling.md
# aggregate-reporting carve-out).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE="$(cd "${HERE}/.." && pwd)"
PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/release-helpers.XXXXXX")" || die "could not make a temp dir"
cleanup() { rm -rf "$TMP"; return 0; }
trap cleanup EXIT

# A stub directory shadowing the helper each wrapper calls, so the wrapper's own
# dispatch is what the check exercises.
stub() { # <name> <exit-code> <stdout> [stderr]
  mkdir -p "$TMP/bin" || die "could not make the stub dir"
  {
    printf '#!/bin/sh\n'
    printf 'printf %%s %s\n' "$(printf '%q' "$3")"
    if [ -n "${4:-}" ]; then printf 'printf %%s %s >&2\n' "$(printf '%q' "$4")"; fi
    printf 'exit %s\n' "$2"
  } > "$TMP/bin/$1" || die "could not write the stub"
  chmod +x "$TMP/bin/$1" || die "could not chmod the stub"
}

run_baseline() { # runs registry-baseline.sh against the stubbed capture helper
  OUT="$(PATH="$TMP/bin:$PATH" bash "$TMP/registry-baseline.sh" ws plug 2>"$TMP/err")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err")"
}

run_landed() {
  OUT="$(PATH="$TMP/bin:$PATH" bash "$TMP/confirm-tessl-landed.sh" ws plug 0.0.1 42 2>"$TMP/err")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err")"
}

# Copy each wrapper beside a stubbed sibling, since both resolve helpers next
# to themselves.
install_wrapper() { # <wrapper> <sibling> <sibling-exit> <sibling-stdout> [stderr]
  cp "$RELEASE/$1" "$TMP/$1" || die "could not copy $1"
  {
    printf '#!/bin/sh\n'
    printf 'printf %%s %s\n' "$(printf '%q' "$4")"
    if [ -n "${5:-}" ]; then printf 'printf %%s %s >&2\n' "$(printf '%q' "$5")"; fi
    printf 'exit %s\n' "$3"
  } > "$TMP/$2" || die "could not write the sibling stub"
  chmod +x "$TMP/$2" || die "could not chmod the sibling stub"
}

echo "▶ registry-baseline.sh" >&2

install_wrapper registry-baseline.sh capture-registry-baseline.sh 0 '{"version":"0.3.9"}'
run_baseline
if [[ $RC -eq 0 && "$OUT" == "0.3.9" ]]; then pass; else fail "a good baseline prints its version, got RC=$RC OUT=$OUT"; fi

install_wrapper registry-baseline.sh capture-registry-baseline.sh 2 '' 'registry unreachable'
run_baseline
if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "could not read the registry baseline"; then
  pass; else fail "a failing capture exits 2, got RC=$RC OUT=$OUT"; fi

install_wrapper registry-baseline.sh capture-registry-baseline.sh 0 '{"other":"field"}'
run_baseline
if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "carries no .version"; then
  pass; else fail "a payload without .version exits 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

install_wrapper registry-baseline.sh capture-registry-baseline.sh 0 'not json'
run_baseline
if [[ $RC -eq 2 && -z "$OUT" ]]; then pass; else fail "a non-JSON payload exits 2, got RC=$RC OUT=$OUT"; fi

echo "▶ confirm-tessl-landed.sh" >&2

install_wrapper confirm-tessl-landed.sh verify-publish-landed.sh 0 '{"ok":true,"current":"0.3.10"}'
run_landed
if [[ $RC -eq 0 && "$OUT" == "0.3.10" ]]; then pass; else fail "a landed publish prints its version, got RC=$RC OUT=$OUT"; fi

install_wrapper confirm-tessl-landed.sh verify-publish-landed.sh 1 '{"ok":false,"reason":"registry did not advance"}'
run_landed
if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "did not land — registry did not advance"; then
  pass; else fail "rc 1 stays a did-not-land verdict, got RC=$RC ERR=$ERRTEXT"; fi

install_wrapper confirm-tessl-landed.sh verify-publish-landed.sh 2 '' 'run still in flight'
run_landed
if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "cannot tell whether the publish landed"; then
  pass; else fail "rc 2 stays indeterminate rather than a failed publish, got RC=$RC ERR=$ERRTEXT"; fi

install_wrapper confirm-tessl-landed.sh verify-publish-landed.sh 0 '{"ok":true}'
run_landed
if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "carries no .current"; then
  pass; else fail "a payload without .current exits 2, got RC=$RC ERR=$ERRTEXT"; fi

echo "─────────────────────────────────────────────" >&2
if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
echo "PASSED: all ${PASS} checks" >&2
