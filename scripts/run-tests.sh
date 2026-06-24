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
# one suite's `set`/`cd`/traps can't leak into the next.
#
# Output contract (rules/script-delegation.md — structured stdout):
#   stdout: one JSON object, the run summary —
#     {"suites": N, "passed": P, "failed": F, "failures": ["<path>", ...]}
#     On a setup error an "error" string field is added and suites=0.
#   stderr: human-readable per-suite progress and each suite's own output.
#
# Usage: scripts/run-tests.sh [base-dir]
#   base-dir   Directory to search for suites. Defaults to the repo root
#              (the script's parent's parent). The optional arg exists so
#              the runner's own test can point it at a fixture tree.
# Exit:  0 if every suite passes, 1 if any suite fails, 2 on setup error
#        (base dir missing, or no suite found).

set -euo pipefail

# JSON string escaper (backslash + double-quote; paths carry no controls).
json_str() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "$s"; }

main() {
  local base="${1:-}"
  if [[ -z "$base" ]]; then
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  if [[ ! -d "$base" ]]; then
    echo "run-tests: base dir not found: $base" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "base dir not found: $base")"
    return 2
  fi

  local suites=()
  local s
  while IFS= read -r s; do
    suites+=("$s")
  done < <(find "$base" -type f -path '*/tests/test_*.sh' | sort)

  if [[ ${#suites[@]} -eq 0 ]]; then
    echo "run-tests: no test suites found under ${base}/**/tests/test_*.sh" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "no test suites found under ${base}/**/tests/test_*.sh")"
    return 2
  fi

  echo "Running ${#suites[@]} test suite(s):" >&2
  echo "" >&2

  local failed=()
  for s in "${suites[@]}"; do
    echo "▶ $s" >&2
    if bash "$s" >&2; then
      echo "  ✓ $s" >&2
    else
      echo "  ✗ $s" >&2
      failed+=("$s")
    fi
    echo "" >&2
  done

  echo "─────────────────────────────────────────────" >&2
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${#failed[@]}/${#suites[@]} suite(s):" >&2
    for s in "${failed[@]}"; do echo "  - $s" >&2; done
  else
    echo "PASSED: all ${#suites[@]} suite(s)." >&2
  fi

  # Structured summary on stdout.
  local failures_json="["
  local i
  for i in "${!failed[@]}"; do
    [[ $i -gt 0 ]] && failures_json+=","
    failures_json+="$(json_str "${failed[$i]}")"
  done
  failures_json+="]"
  printf '{"suites":%d,"passed":%d,"failed":%d,"failures":%s}\n' \
    "${#suites[@]}" "$(( ${#suites[@]} - ${#failed[@]} ))" "${#failed[@]}" "$failures_json"

  [[ ${#failed[@]} -eq 0 ]]
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
