#!/usr/bin/env bash
# Outcome tests: a hook and a skill script resolve their own directory when
# invoked by a relative path under a hostile CDPATH (#613).
#
# With CDPATH set, a relative `cd` searches CDPATH first and prints the
# directory it chose, so the captured script directory named a decoy and
# gained an extra line. Each case stages one script in a fixture plugin tree,
# points CDPATH at a decoy holding a same-named directory, runs the script by
# a relative path, and asserts the directory it handed on.
#
# One hook and one skill script stand in for every site that shares the
# derivation. The harness drops `set -e` to aggregate results, so every
# fixture command is checked explicitly (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Covers:
#   1. hooks/herdr-supervision-stop.sh -> puts <plugin>/skills/herdr-foreman
#      on PYTHONPATH.
#   2. skills/herdr-foreman/foreman.sh -> puts its own directory on PYTHONPATH.
#
# Run: bash hooks/tests/test_cdpath_script_dir.sh
# stdout: one JSON line {"suite","passed","failed"}; progress goes to stderr.
set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "  pass: $1" >&2; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi; return 0; }

# The first PYTHONPATH entry the stand-in interpreter or module recorded.
recorded() { # <marker>
  if [[ -f "$1" ]]; then cat -- "$1"; else printf '<not reached>'; fi
}

run_suite() {
  local repo
  repo="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || die "cannot resolve the repo root"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/cdpath-script-dir.XXXXXX")" || die "mktemp failed"
  trap cleanup EXIT
  local plugin="$TMP/plugin" decoy="$TMP/decoy" marker="$TMP/marker" rc err want

  mkdir -p "$plugin/hooks" "$plugin/skills/herdr-foreman/foreman" "$decoy/hooks" "$decoy/herdr-foreman" \
    || die "create the fixture tree"
  cp "$repo/hooks/herdr-supervision-stop.sh" "$plugin/hooks/" || die "stage herdr-supervision-stop.sh"
  cp "$repo/skills/herdr-foreman/foreman.sh" "$plugin/skills/herdr-foreman/" || die "stage foreman.sh"

  # 1. The stand-in supervision module records the path it was imported under.
  cat > "$plugin/skills/herdr-foreman/foreman/supervision_hook.py" <<'PY' || die "write the supervision stand-in"
import os

with open(os.environ["MARKER"], "w", encoding="utf-8") as handle:
    handle.write(os.environ["PYTHONPATH"].split(":")[0])
PY
  : > "$plugin/skills/herdr-foreman/foreman/__init__.py" || die "write the package marker"
  want="$(CDPATH='' cd -- "$plugin" && pwd)/skills/herdr-foreman" || die "resolve $plugin"
  rm -f "$marker" || die "reset $marker"
  (cd "$plugin" && env -u PYTHONPATH CDPATH="$decoy" MARKER="$marker" bash hooks/herdr-supervision-stop.sh </dev/null >/dev/null 2>"$TMP/err"); rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 0 && "$(recorded "$marker")" == "$want" ]]; then
    pass "hook resolves the plugin root under CDPATH"
  else
    fail "herdr-supervision-stop.sh: expected PYTHONPATH $want, got $(recorded "$marker") rc=$rc err=$err"
  fi

  # 2. A stand-in interpreter answers the version probe, then records the
  #    PYTHONPATH it was handed.
  cat > "$TMP/fakepy" <<'FAKE' || die "write the stand-in interpreter"
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then printf '3.12\n'; exit 0; fi
printf '%s' "${PYTHONPATH%%:*}" > "${MARKER:?}"
FAKE
  chmod +x "$TMP/fakepy" || die "chmod the stand-in interpreter"
  want="$(CDPATH='' cd -- "$plugin/skills/herdr-foreman" && pwd)" || die "resolve the staged skill dir"
  rm -f "$marker" || die "reset $marker"
  (cd "$plugin/skills" && env -u PYTHONPATH CDPATH="$decoy" MARKER="$marker" PY_BIN="$TMP/fakepy" bash herdr-foreman/foreman.sh state >/dev/null 2>"$TMP/err"); rc=$?
  err="$(cat "$TMP/err")"
  if [[ $rc -eq 0 && "$(recorded "$marker")" == "$want" ]]; then
    pass "skill script resolves its own directory under CDPATH"
  else
    fail "foreman.sh: expected PYTHONPATH $want, got $(recorded "$marker") rc=$rc err=$err"
  fi

  printf '{"suite":"test_cdpath_script_dir.sh","passed":%d,"failed":%d}\n' "$PASS" "$FAIL"
  [[ $FAIL -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_suite
fi
