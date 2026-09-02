#!/usr/bin/env bash
# Guard the guard: every Python suite must run all of its classes as a script.
#
# scripts/run-tests.sh executes each suite as `python3 <file>`, and its contract
# note says suites self-drive through the entry-point guard. That makes the
# guard's POSITION load-bearing: `unittest.main()` sitting mid-file runs the
# classes defined above it and silently skips every class below, so the runner
# reports a green suite while tests never execute. Three suites drifted into
# exactly that state, hiding 70 tests.
#
# Two independent assertions per suite, because either alone can pass while the
# defect is present:
#   1. The entry-point guard is the LAST top-level statement in the file.
#   2. `python3 <file>` reports the same `Ran N tests` as `python3 -m unittest
#      <module>`, which collects every class regardless of guard position.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash skills/herdr-teamlead/tests/test_suite_entrypoints.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Echo the count from a `Ran N tests` line, or nothing when the run had none.
ran_count() { # <output>
  printf '%s' "$1" | grep -oE '^Ran [0-9]+ tests?' | grep -oE '[0-9]+' | tail -1
}

main() {
  SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  command -v python3 >/dev/null 2>&1 || die "python3 required for these tests"

  local suites=()
  local f
  while IFS= read -r f; do
    [[ -n "$f" ]] && suites+=("$f")
  done < <(find "$SKILL_DIR/tests" -maxdepth 1 -type f -name 'test_*.py' | sort)
  [[ ${#suites[@]} -gt 0 ]] || die "no Python suites found under $SKILL_DIR/tests"

  FAIL=0; PASS=0

  local name module script_out module_out script_n module_n last
  for f in "${suites[@]}"; do
    name="$(basename "$f")"
    module="tests.${name%.py}"

    # 1. The guard is the last top-level statement. Parsed with ast rather than
    #    grepped, so a guard inside a class or a string cannot satisfy it.
    last="$(cd "$SKILL_DIR" && python3 - "$f" <<'PY'
import ast
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    tree = ast.parse(handle.read())
node = tree.body[-1] if tree.body else None
is_guard = (
    isinstance(node, ast.If)
    and ast.unparse(node.test).replace("'", '"') == '__name__ == "__main__"'
)
print("guard" if is_guard else "other")
PY
)" || last="parse-error"
    if [[ "$last" == "guard" ]]; then
      pass
    else
      fail "${name}: the entry-point guard is not the last top-level statement (found: ${last}) — every class after it is skipped when the suite runs as a script"
    fi

    # 2. The script run collects as many tests as the module run. A guard in
    #    the wrong place shows up here as a smaller script count.
    script_out="$(cd "$SKILL_DIR" && python3 "$f" 2>&1)"
    module_out="$(cd "$SKILL_DIR" && PYTHONPATH="$SKILL_DIR" python3 -m unittest "$module" 2>&1)"
    script_n="$(ran_count "$script_out")"
    module_n="$(ran_count "$module_out")"
    if [[ -z "$script_n" || -z "$module_n" ]]; then
      fail "${name}: could not read a test count (script='${script_n}' module='${module_n}')"
    elif [[ "$script_n" == "$module_n" ]]; then
      pass
    else
      fail "${name}: running it as a script collected ${script_n} tests, the module collects ${module_n} — $(( module_n - script_n )) never execute under scripts/run-tests.sh"
    fi
  done

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
