#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/resolve-gates.sh.
#
# Real directory trees per case, so the fixtures share no state and run in any
# order (rules/testing-standards.md Independence).
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. No declaration        -> declared:false, workflows still reported, exit 0.
#   2. A declaration         -> its instructions, runners and notes come back.
#   3. A rotted declaration  -> the absent paths land in `missing`, not silence.
#   4. Malformed JSON        -> exit 2, a repair, never an empty map.
#   5. Wrong schema / keys   -> exit 2, named.
#   6. Bad value types       -> exit 2, named.
#   7. Workflows             -> .yml and .yaml, sorted, relative; absent dir is [].
#   8. Usage / bad checkout  -> exit 2, no JSON.
#
# No case asserts a filename this script recognises, because it recognises none.
# An earlier draft matched a hardcoded list of names, which is the enumerated
# failure #480 retired: the set of filenames meaning "runner" is not enumerable.

set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/resolve-gates.sh" || die "cannot resolve the script"

run() { # <checkout>
  OUT="$(bash "$SCRIPT" "$1" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

list() { printf '%s' "$1" | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)[sys.argv[1]]))' "$2"; }
field() { printf '%s' "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(eval(sys.argv[1])))' "$2"; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi; return 0; }

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/resolve-gates-tests.XXXXXX")" || die "mktemp"
  trap cleanup EXIT
  ERRFILE="$TMP/err"

  echo "▶ an undeclared repo" >&2

  mkdir -p "$TMP/bare/.github/workflows" || die "mkdir bare"
  printf 'x\n' > "$TMP/bare/.github/workflows/tests.yml" || die "write workflow"
  printf 'x\n' > "$TMP/bare/Makefile" || die "write Makefile"
  run "$TMP/bare"
  # A Makefile sits there and is deliberately NOT reported: nothing guesses.
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["declared"]')" == "false" ]] \
     && [[ "$(list "$OUT" runners)" == "" ]] \
     && [[ "$(list "$OUT" workflows)" == ".github/workflows/tests.yml" ]] \
     && [[ "$(field "$OUT" 'd["brief"]')" == '"undeclared"' ]]; then
    pass; else fail "undeclared reports workflows and guesses nothing, got RC=$RC OUT=$OUT"; fi

  echo "▶ a declared repo" >&2

  mkdir -p "$TMP/decl/.herdr" "$TMP/decl/scripts" || die "mkdir decl"
  printf 'x\n' > "$TMP/decl/AGENTS.md" || die "write AGENTS.md"
  printf 'x\n' > "$TMP/decl/scripts/verify.sh" || die "write verify.sh"
  cat > "$TMP/decl/.herdr/gates.json" <<'JSON' || die "write declaration"
{"schema_version": 1, "instructions": ["AGENTS.md"],
 "runners": ["scripts/verify.sh"], "notes": "run verify before pushing"}
JSON
  run "$TMP/decl"
  # `scripts/verify.sh` is a name no hardcoded list would hold. A declaration
  # needs none.
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["declared"]')" == "true" ]] \
     && [[ "$(list "$OUT" instructions)" == "AGENTS.md" ]] \
     && [[ "$(list "$OUT" runners)" == "scripts/verify.sh" ]] \
     && [[ "$(field "$OUT" 'd["notes"]')" == '"run verify before pushing"' ]]; then
    pass; else fail "a declaration is reported as written, got RC=$RC OUT=$OUT"; fi
  # The GATES value arrives rendered: the lead copies it, never builds it.
  # The backticks are literal Markdown the brief carries, not a command substitution.
  # shellcheck disable=SC2016
  expected='"- Instructions: `AGENTS.md`\n- Runners: `scripts/verify.sh`\n- Notes: run verify before pushing"'
  if [[ "$(field "$OUT" 'd["brief"]')" == "$expected" ]]; then
    pass; else fail "a declared repo's brief is the ready GATES list, got OUT=$OUT"; fi

  echo "▶ a declaration that rotted" >&2

  mkdir -p "$TMP/rot/.herdr" || die "mkdir rot"
  cat > "$TMP/rot/.herdr/gates.json" <<'JSON' || die "write rotted declaration"
{"schema_version": 1, "instructions": ["GONE.md"], "runners": ["scripts/gone.sh"]}
JSON
  run "$TMP/rot"
  # A rotted path is named in `missing` and never handed to a worker.
  if [[ $RC -eq 0 ]] && [[ "$(list "$OUT" missing)" == "GONE.md,scripts/gone.sh" ]] \
     && [[ "$(field "$OUT" 'd["brief"]')" == '"undeclared"' ]]; then
    pass; else fail "a declared path that is gone lands in missing, got RC=$RC OUT=$OUT"; fi

  echo "▶ a declaration that cannot be trusted" >&2

  mkdir -p "$TMP/bad/.herdr" || die "mkdir bad"
  for broken in '{broken' '{"schema_version": 2}' '{"schema_version": 1, "extra": 1}' \
                '{"schema_version": 1, "instructions": "AGENTS.md"}' \
                '{"schema_version": 1, "runners": [""]}' \
                '{"schema_version": 1, "notes": "  "}' \
                '{"schema_version": 1, "runners": ["../outside.sh"]}' \
                '{"schema_version": 1, "instructions": ["/etc/hosts"]}'; do
    printf '%s\n' "$broken" > "$TMP/bad/.herdr/gates.json" || die "write broken declaration"
    run "$TMP/bad"
    if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'resolve-gates:'; then
      pass; else fail "'$broken' must be a repair, never an empty map, got RC=$RC OUT=$OUT"; fi
  done

  echo "▶ workflows and usage" >&2

  mkdir -p "$TMP/wf/.github/workflows" || die "mkdir wf"
  printf 'x\n' > "$TMP/wf/.github/workflows/b.yaml" || die "write b"
  printf 'x\n' > "$TMP/wf/.github/workflows/a.yml" || die "write a"
  mkdir -p "$TMP/wf/.github/workflows/nested.yml" || die "mkdir nested"
  run "$TMP/wf"
  if [[ $RC -eq 0 ]] && [[ "$(list "$OUT" workflows)" == ".github/workflows/a.yml,.github/workflows/b.yaml" ]]; then
    pass; else fail "workflows are files, sorted and relative, got $(list "$OUT" workflows)"; fi

  mkdir -p "$TMP/noghub" || die "mkdir noghub"
  run "$TMP/noghub"
  if [[ $RC -eq 0 ]] && [[ "$(list "$OUT" workflows)" == "" ]] \
     && ! printf '%s' "$OUT" | grep -q '\*'; then
    pass; else fail "an absent .github is an empty list, never a literal glob, got RC=$RC OUT=$OUT"; fi

  OUT="$(bash "$SCRIPT" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'usage'; then
    pass; else fail "no argument is a usage error, got RC=$RC OUT=$OUT"; fi

  run "$TMP/does-not-exist"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'not a directory'; then
    pass; else fail "an absent checkout is a tool error, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
