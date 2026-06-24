#!/usr/bin/env bash
# Discover and run every module's bash unit-test suite, in one pass.
#
# rules/testing-standards.md mandates that every module ship tests, but
# nothing executed them: the publish pipeline ran lint + skill-review +
# publish, so a red suite on `main` stayed invisible and a regression
# could ship green. This runner is the missing enforcement — CI calls it
# on every PR (tests.yml) and again before publish (publish.yml), so a
# failing suite blocks the merge and the release.
#
# Discovers <base>/**/tests/test_*.sh, runs each in its own subshell so
# one suite's `set`/`cd`/traps can't leak into the next, prints a
# per-suite summary, and exits non-zero if any suite fails.
#
# Usage: scripts/run-tests.sh [base-dir]
#   base-dir   Directory to search for suites. Defaults to the repo root
#              (the script's parent's parent). The optional arg exists so
#              the runner's own test can point it at a fixture tree.
# Out:   per-suite progress on stdout; failing-suite names on stderr.
# Exit:  0 if every suite passes, 1 if any suite fails, 2 on setup error
#        (base dir missing, or no suite found).

set -euo pipefail

main() {
  local base="${1:-}"
  if [[ -z "$base" ]]; then
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  [[ -d "$base" ]] || { echo "run-tests: base dir not found: $base" >&2; return 2; }

  local suites=()
  local s
  while IFS= read -r s; do
    suites+=("$s")
  done < <(find "$base" -type f -path '*/tests/test_*.sh' | sort)

  if [[ ${#suites[@]} -eq 0 ]]; then
    echo "run-tests: no test suites found under ${base}/**/tests/test_*.sh" >&2
    return 2
  fi

  echo "Running ${#suites[@]} test suite(s):"
  echo ""

  local failed=()
  for s in "${suites[@]}"; do
    echo "▶ $s"
    if bash "$s"; then
      echo "  ✓ $s"
    else
      echo "  ✗ $s" >&2
      failed+=("$s")
    fi
    echo ""
  done

  echo "─────────────────────────────────────────────"
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${#failed[@]}/${#suites[@]} suite(s):" >&2
    for s in "${failed[@]}"; do echo "  - $s" >&2; done
    return 1
  fi
  echo "PASSED: all ${#suites[@]} suite(s)."
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
