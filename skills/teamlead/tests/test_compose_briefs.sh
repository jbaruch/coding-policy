#!/usr/bin/env bash
# Outcome-based tests for skills/teamlead/compose-briefs.sh.
#
# Templates and values are built in the test, so the assertions are about the
# script's own behavior rather than about the shipped templates' current text
# (rules/testing-standards.md — fixtures built in setup, no binary fixtures).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Substitution     -> shared + per-role values land in the right files.
#   2. Role wins        -> a role value overrides the same shared key.
#   3. Emitted paths    -> the JSON names COMMON.md and every brief.
#   4. Unfilled         -> a placeholder with no value is exit 2, nothing written.
#   5. Unknown key      -> a value no template uses is exit 2, nothing written.
#   6. Atomicity        -> a failing second role leaves the first unwritten.
#   7. Missing template -> exit 1 naming the path.
#   8. Bad JSON         -> exit 1.
#   9. Idempotent       -> a second identical run rewrites the same content.
#  10. Usage            -> exit 1 with a usage line.
#
# Run: bash skills/teamlead/tests/test_compose_briefs.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_templates() { # <dir>
  mkdir -p "$1" || die "could not create $1"
  printf 'Checkout: {{SHARED_CHECKOUT}}\nAuthority: {{AUTHORITY_STATEMENT}}\n' > "$1/COMMON.md" \
    || die "could not write COMMON.md"
  printf 'Dev on {{BRANCH}} in {{WORKTREE}} for {{ISSUE}}\nReport: {{REPORT}}\n' > "$1/brief-developer.md" \
    || die "could not write brief-developer.md"
  printf 'Tester for {{ISSUE}}\nReport: {{REPORT}}\n' > "$1/brief-tester.md" \
    || die "could not write brief-tester.md"
}

run() { # <templates> <values-file> <outdir>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(bash "$SCRIPT" "$1" "$2" "$3" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compose-briefs.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "compose-briefs.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t teamlead-compose-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  TPL="$TMP/templates"; mk_templates "$TPL"
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1 + 3. A complete round.
  local v1="$TMP/v1.json" o1="$TMP/out1"
  cat > "$v1" <<'JSON' || die "could not write $v1"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "owner of jbaruch/x"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt/dev", "ISSUE": "#7", "REPORT": "/r/dev.md"},
    "tester": {"ISSUE": "#7", "REPORT": "/r/test.md"}
  }
}
JSON
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] \
     && grep -q "Checkout: /repo" "$o1/COMMON.md" \
     && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md" \
     && grep -q "Report: /r/test.md" "$o1/brief-tester.md"; then
    pass; else fail "substitution: got RC=$RC ERR=$ERRTEXT"; fi
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg o "$o1" '
      .common == ($o + "/COMMON.md")
      and .briefs.developer == ($o + "/brief-developer.md")
      and .briefs.tester == ($o + "/brief-tester.md")' >/dev/null 2>&1; then
    pass; else fail "emitted paths: got OUT=$OUT"; fi

  # 2. A role value beats the shared one.
  local v2="$TMP/v2.json" o2="$TMP/out2"
  cat > "$v2" <<'JSON' || die "could not write $v2"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a", "ISSUE": "#1", "REPORT": "/shared.md"},
  "roles": {"tester": {"ISSUE": "#9"}}
}
JSON
  run "$TPL" "$v2" "$o2"
  if [[ $RC -eq 0 ]] && grep -q "Tester for #9" "$o2/brief-tester.md"; then
    pass; else fail "role override: got RC=$RC ERR=$ERRTEXT"; fi

  # 4. A placeholder with no value is a brief that lies to a worker.
  local v4="$TMP/v4.json" o4="$TMP/out4"
  cat > "$v4" <<'JSON' || die "could not write $v4"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"developer": {"BRANCH": "feat/x", "ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v4" "$o4"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "WORKTREE"; then
    pass; else fail "unfilled: expected exit 2 naming WORKTREE, got RC=$RC ERR=$ERRTEXT"; fi
  if [[ ! -e "$o4/brief-developer.md" ]]; then
    pass; else fail "unfilled: nothing may be written on a validation failure"; fi

  # 5. A key no template uses is a value the lead believes it sent.
  local v5="$TMP/v5.json" o5="$TMP/out5"
  cat > "$v5" <<'JSON' || die "could not write $v5"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "/r.md", "REPORTS_DIRR": "/typo"}}
}
JSON
  run "$TPL" "$v5" "$o5"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "REPORTS_DIRR"; then
    pass; else fail "unknown key: expected exit 2 naming the typo, got RC=$RC ERR=$ERRTEXT"; fi

  # 6. A round is all-or-nothing: a later role failing leaves no earlier file.
  local v6="$TMP/v6.json" o6="$TMP/out6"
  cat > "$v6" <<'JSON' || die "could not write $v6"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt", "ISSUE": "#7", "REPORT": "/r.md"},
    "tester": {"ISSUE": "#7"}
  }
}
JSON
  run "$TPL" "$v6" "$o6"
  if [[ $RC -eq 2 ]] && [[ ! -e "$o6/brief-developer.md" ]] && [[ ! -e "$o6/COMMON.md" ]]; then
    pass; else fail "atomicity: a failed round left files behind (RC=$RC)"; fi

  # 7. A role with no template is named, not guessed at.
  local v7="$TMP/v7.json" o7="$TMP/out7"
  cat > "$v7" <<'JSON' || die "could not write $v7"
{"shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"}, "roles": {"scribe": {}}}
JSON
  run "$TPL" "$v7" "$o7"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "brief-scribe.md"; then
    pass; else fail "missing template: expected exit 1 naming it, got RC=$RC ERR=$ERRTEXT"; fi

  # 8. Malformed values.
  local v8="$TMP/v8.json"
  printf '{broken' > "$v8" || die "could not write $v8"
  run "$TPL" "$v8" "$TMP/out8"
  if [[ $RC -eq 1 && -z "$OUT" ]]; then
    pass; else fail "bad JSON: expected exit 1, got RC=$RC"; fi

  # 9. Re-running a round rewrites the same content (idempotent).
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md"; then
    pass; else fail "idempotent: second run changed the output (RC=$RC)"; fi

  # 10. Usage.
  OUT="$(bash "$SCRIPT" 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e10"; then
    pass; else fail "usage: expected exit 1 with a usage line, got RC=$RC"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
