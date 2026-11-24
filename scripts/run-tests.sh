#!/usr/bin/env bash
# Discover and run every module's unit-test suite, in one pass.
#
# rules/testing-standards.md mandates that every module ship tests, but
# nothing executed them: the publish pipeline ran lint + skill-review +
# publish, so a red suite on `main` stayed invisible and a regression
# could ship green. This runner is the missing enforcement — CI calls it
# on every PR (tests.yml) and again before publish (publish.yml), so a
# failing suite blocks the merge and the release.
#
# Discovers <base>/**/tests/test_*.sh and <base>/**/tests/test_*.py, and
# runs each in its own process (`bash file` / `python3 file`) so one suite's
# `set`/`cd`/traps/interpreter state can't leak into the next. Discovery
# covers BOTH languages deliberately: a `.sh`-only
# glob silently orphaned every Python suite in the repo — they looked
# covered while never executing in CI, including one wired into the publish
# path. Each suite self-drives (a shell harness prints its own summary and
# exits non-zero; a unittest module does the same via its entry-point
# guard), so dispatch is by extension and nothing else.
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
# Exit:  0 if every suite passes, 1 if any suite fails, 2 on setup error.

set -euo pipefail

# Interpreters are overridable so a runner with a differently-named binary
# (python3.11, a venv shim) can point at it without editing this file, and
# so the missing-interpreter path is testable without breaking PATH for the
# harness itself.
SH_BIN="${SH_BIN:-bash}"
PY_BIN="${PY_BIN:-python3}"
export PYTHONDONTWRITEBYTECODE=1

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

XDG_RUN_HOME=""

remove_xdg_run_home() {
  if [[ -n "$XDG_RUN_HOME" ]] && ! rm -rf "$XDG_RUN_HOME"; then
    echo "run-tests: could not remove the per-run XDG home ${XDG_RUN_HOME}; delete it by hand" >&2
  fi
  return 0
}

main() {
  local base="${1:-}"
  if [[ -z "$base" ]]; then
    base="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
  local tmplist
  if ! tmplist="$(mktemp)"; then
    echo "run-tests: could not create the suite list (mktemp failed); check TMPDIR" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' "$(json_str "mktemp failed")"
    return 2
  fi
  # `-path '*/tests/test_*.sh'` narrows to a tests/ dir, but `find`'s `*`
  # matches `/` too, so it still matches a NESTED file like
  # `tests/test_fixtures/helper.sh`. The direct-child requirement can't be
  # expressed portably in `-path`, so discovery casts wide here and the
  # collection loop below enforces `dirname == */tests`.
  if ! find "$base" -type f \( -path '*/tests/test_*.sh' -o -path '*/tests/test_*.py' \) -print0 | sort -z > "$tmplist"; then
    rm -f "$tmplist"
    echo "run-tests: suite discovery (find) failed under $base" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "suite discovery failed under $base")"
    return 2
  fi

  # A suite is a DIRECT child of a `tests/` directory. Drop anything nested
  # deeper (a fixture/helper named test_* under tests/test_fixtures/), which
  # the wide `find` match above still lets through.
  local suites=()
  local s
  while IFS= read -r -d '' s; do
    [[ -n "$s" ]] || continue
    [[ "$(dirname "$s")" == */tests ]] || continue
    suites+=("$s")
  done < "$tmplist"
  rm -f "$tmplist"

  if [[ ${#suites[@]} -eq 0 ]]; then
    local message="no test suites found under ${base}/**/tests/test_*.{sh,py}"
    echo "run-tests: $message" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "$message")"
    return 2
  fi

  # Each suite runs against its own empty XDG home, never the operator's and
  # never an earlier suite's: anything a test does not pass explicitly would
  # otherwise read, and possibly refuse on, the machine's real state and
  # config, or files a previous suite left behind.
  if ! XDG_RUN_HOME="$(mktemp -d)"; then
    echo "run-tests: could not create the per-run XDG home (mktemp -d failed); check TMPDIR" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' "$(json_str "mktemp -d failed")"
    return 2
  fi
  trap remove_xdg_run_home EXIT

  echo "Running ${#suites[@]} test suite(s):" >&2
  echo "" >&2

  local failed=() interp n=0
  for s in "${suites[@]}"; do
    echo "▶ $s" >&2
    n=$((n + 1))
    if ! mkdir -p "$XDG_RUN_HOME/$n/state" "$XDG_RUN_HOME/$n/config"; then
      echo "run-tests: could not create the XDG home for ${s} under ${XDG_RUN_HOME}/${n}; check TMPDIR" >&2
      printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' "$(json_str "mkdir of a per-suite XDG home failed")"
      return 2
    fi
    export XDG_STATE_HOME="$XDG_RUN_HOME/$n/state" XDG_CONFIG_HOME="$XDG_RUN_HOME/$n/config"

    # Discovery yields only .sh and .py suites.
    if [[ "$s" == *.sh ]]; then
      interp="$SH_BIN"
    else
      interp="$PY_BIN"
    fi
    if ! command -v "$interp" >/dev/null; then
      echo "run-tests: '${interp}' not found on PATH — cannot run ${s}; install it on this runner (this is a setup error, not a test failure)" >&2
      echo "─────────────────────────────────────────────" >&2
      echo "SETUP ERROR: cannot run ${s} — see stderr above" >&2
      printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
        "$(json_str "cannot run ${s}: interpreter missing or unknown suite type")"
      return 2
    fi

    if "$interp" "$s" >&2; then
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
