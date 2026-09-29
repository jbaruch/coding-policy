#!/usr/bin/env bash
# Outcome tests: a release script still reaches its siblings when its own
# directory's name ends in a newline (#592).
#
# A `$(dirname ...)` capture strips that newline, so the script looked for its
# siblings in a directory that does not exist. Each case stages one script
# under such a directory beside stub siblings that record being run (or, for a
# sourced library, being sourced), then asserts the record: the outcome is the
# sibling reached, whatever the script does next.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. registry-baseline.sh      -> runs capture-registry-baseline.sh.
#   2. confirm-tessl-landed.sh   -> runs verify-publish-landed.sh.
#   3. verify-publish-landed.sh  -> sources version-compare.sh and
#                                   registry-version.sh.
#   4. confirm-publish-landed.sh -> runs registry-version.sh, gate passes.
#   5. smart-publish.sh          -> runs registry-version.sh.
#   6. watch-pr-reviews.sh       -> runs poll-pr-reviews.sh.
#   7. poll-pr-reviews.sh        -> sources copilot-run.sh.
#   8. request-copilot-review.sh -> sources copilot-run.sh.
#
# Run: bash skills/release/tests/test_script_dir_newline.sh
set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi; return 0; }

NL=$'\n'

# A run-stub sibling: records its name in $MARKER, prints <stdout>, exits <rc>.
stub() { # <path> <rc> <stdout>
  printf '%s' "$3" > "$1.out" || die "write the stub output for $1"
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    # shellcheck disable=SC2016  # The format string is the stub's source: ${MARKER:?} expands in the stub.
    printf 'printf "%%s\\n" %q >> "${MARKER:?}"\n' "${1##*/}"
    printf 'cat -- %q\n' "$1.out"
    printf 'exit %d\n' "$2"
  } > "$1" || die "write the stub $1"
}

# A sourced-library stub: records its name in $MARKER and defines <function>.
lib_stub() { # <path> <function>
  {
    printf '#!/usr/bin/env bash\n'
    # shellcheck disable=SC2016  # The format string is the stub's source: ${MARKER:?} expands in the stub.
    printf 'printf "%%s\\n" %q >> "${MARKER:?}"\n' "${1##*/}"
    printf '%s() { return 1; }\n' "$2"
  } > "$1" || die "write the library stub $1"
}

# A fresh staging directory whose name ends in a newline, holding the script.
stage() { # <case> <script> -> STAGE
  STAGE="$TMP/$1/release${NL}"
  mkdir -p "$STAGE" || die "create the staging directory for $1"
  cp "$REAL/$2" "$STAGE/$2" || die "copy $2 into the staging directory"
}

reached() { # <sibling-name>
  [[ -f "$MARKER" ]] && grep -qxF "$1" "$MARKER"
}

run() { # <script> [args...] -> OUT, RC, ERRTEXT
  local script="$1"; shift
  : > "$MARKER" || die "reset $MARKER"
  OUT="$(env -u WATCH_PR_REVIEWS_POLL_CMD MARKER="$MARKER" PATH="$TMP/bin:$PATH" \
    bash "$STAGE/$script" "$@" 2>"$TMP/err")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err")"
}

main() {
  REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the release skill dir"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/script-dir-newline.XXXXXX")" || die "mktemp failed"
  trap cleanup EXIT
  MARKER="$TMP/marker"

  if ! mkdir "$TMP/probe${NL}" 2>"$TMP/probe.err"; then
    echo "skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/probe.err"))" >&2
    exit 0
  fi

  # A `tessl` on PATH: smart-publish.sh refuses to start without one, and this
  # stand-in is never reached before the sibling under test.
  mkdir -p "$TMP/bin" || die "create $TMP/bin"
  stub "$TMP/bin/tessl" 9 ''

  # 1.
  stage baseline registry-baseline.sh
  stub "$STAGE/capture-registry-baseline.sh" 0 '{"version":"9.9.9"}'
  run registry-baseline.sh ws plugin
  if [[ $RC -eq 0 && "$OUT" == "9.9.9" ]] && reached capture-registry-baseline.sh; then
    pass; else fail "registry-baseline.sh: expected 9.9.9 from its sibling, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 2.
  stage tessl-landed confirm-tessl-landed.sh
  stub "$STAGE/verify-publish-landed.sh" 0 '{"current":"9.9.9"}'
  run confirm-tessl-landed.sh ws plugin 1.0.0 42
  if [[ $RC -eq 0 && "$OUT" == "9.9.9" ]] && reached verify-publish-landed.sh; then
    pass; else fail "confirm-tessl-landed.sh: expected 9.9.9 from its sibling, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 3. Both libraries are sourced at load time; the usage error that follows
  #    shows the script got past them.
  stage verify verify-publish-landed.sh
  lib_stub "$STAGE/version-compare.sh" version_gt
  lib_stub "$STAGE/registry-version.sh" registry_version
  run verify-publish-landed.sh
  if [[ $RC -eq 2 && "$ERRTEXT" == *usage:* ]] && reached version-compare.sh && reached registry-version.sh; then
    pass; else fail "verify-publish-landed.sh: expected both libraries sourced, got RC=$RC ERR=$ERRTEXT"; fi

  # 4.
  stage confirm confirm-publish-landed.sh
  cp "$REAL/version-compare.sh" "$STAGE/version-compare.sh" || die "copy version-compare.sh"
  stub "$STAGE/registry-version.sh" 0 '{"version":"1.0.1"}'
  run confirm-publish-landed.sh ws plugin 1.0.0 success false
  if [[ $RC -eq 0 ]] && reached registry-version.sh \
     && printf '%s' "$OUT" | jq -e '.gate == "pass" and .current == "1.0.1"' >/dev/null; then
    pass; else fail "confirm-publish-landed.sh: expected a pass read from its sibling, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 5. Auto-bump reads the registry through its sibling before publishing.
  stage publish smart-publish.sh
  stub "$STAGE/registry-version.sh" 7 ''
  mkdir -p "$TMP/publish/plugin/.tessl-plugin" || die "create the plugin fixture"
  printf '{"name":"ws/plugin","version":"1.0.0"}\n' > "$TMP/publish/plugin/.tessl-plugin/plugin.json" \
    || die "write the plugin manifest"
  run smart-publish.sh auto-bump "$TMP/publish/plugin" main branch
  if [[ $RC -eq 1 && "$ERRTEXT" == *"registry-version.sh exit 7"* ]] && reached registry-version.sh; then
    pass; else fail "smart-publish.sh: expected its sibling's exit 7 reported, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 6.
  stage watch watch-pr-reviews.sh
  stub "$STAGE/poll-pr-reviews.sh" 3 ''
  run watch-pr-reviews.sh owner repo 1
  if [[ $RC -eq 2 && "$ERRTEXT" == *"poll-pr-reviews.sh failed"* ]] && reached poll-pr-reviews.sh; then
    pass; else fail "watch-pr-reviews.sh: expected its sibling run, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 7-8. The library is sourced at load time; the usage error that follows
  #      shows the script got past it.
  for _script in poll-pr-reviews.sh request-copilot-review.sh; do
    stage "copilot-run-${_script%.sh}" "$_script"
    lib_stub "$STAGE/copilot-run.sh" copilot_run_in_flight
    run "$_script"
    if [[ $RC -eq 2 && "$ERRTEXT" == *usage:* ]] && reached copilot-run.sh; then
      pass; else fail "${_script}: expected copilot-run.sh sourced, got RC=$RC ERR=$ERRTEXT"; fi
  done

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
