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

# JSON string escaper. A Unix path may contain any byte except NUL and
# '/', so escape the JSON-mandatory characters AND the C0 control set
# (newline/tab/CR/etc.) rather than assuming paths are control-free.
json_str() {
  local s="$1" out="" i ch ord
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    printf -v ord '%d' "'$ch"
    if (( ord >= 0 && ord < 32 )); then
      case "$ch" in
        $'\n') out+='\n' ;;
        $'\t') out+='\t' ;;
        $'\r') out+='\r' ;;
        $'\b') out+='\b' ;;
        $'\f') out+='\f' ;;
        *) out+="$(printf '\\u%04x' "$ord")" ;;
      esac
    else
      out+="$ch"
    fi
  done
  printf '"%s"' "$out"
}

# Best-effort temp cleanup. Under `set -e` a failing bare `rm` on the error
# branch would abort before the diagnostic below is emitted; warn and
# continue instead, never silence (rules/error-handling.md Shell Error Handling).
discard() {
  local f="$1"
  if [[ -n "$f" ]] && ! rm -f "$f"; then
    echo "run-tests: warning: could not remove temp file ${f} — remove it by hand" >&2
  fi
}

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

  # NUL-delimited discovery via a temp file. NUL is the only byte a Unix
  # path can't contain, so `-print0`/`sort -z`/`read -d ''` survives paths
  # with spaces or newlines (a newline-delimited pipe would split them).
  # A temp file (not process substitution) keeps `find`'s exit observable:
  # `set -o pipefail` propagates a `find` failure through `| sort -z` to
  # the redirection, and the `if !` catches it — a command substitution
  # can't be used because bash strips NUL bytes from its output.
  local tmplist; tmplist="$(mktemp)"
  if ! find "$base" -type f -path '*/tests/test_*.sh' -print0 | sort -z > "$tmplist"; then
    discard "$tmplist"
    echo "run-tests: suite discovery (find) failed under $base" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "suite discovery failed under $base")"
    return 2
  fi

  local suites=()
  local s
  while IFS= read -r -d '' s; do
    [[ -n "$s" ]] && suites+=("$s")
  done < "$tmplist"
  discard "$tmplist"

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
