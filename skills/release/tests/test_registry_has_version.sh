#!/usr/bin/env bash
# Outcome-based tests for registry-has-version.sh — the exact-version existence
# probe the landed-after-error tolerance rests on (smart-publish.sh).
#
# The load-bearing property is the THREE-way answer, not a boolean: a publish
# that exited non-zero may have landed anyway, and only a definitive "yes" may
# green it. "No" and "cannot tell" must both keep the run red, and they must be
# distinguishable from each other in the diagnostics — a 404 is an answer, an
# auth or network failure is not (rules/error-handling.md — an expected
# non-result is not a tool failure).
#
# Body shapes are the live ones, probed against jbaruch/coding-policy:
#   present -> exit 0, {"data":{"attributes":{"version":"x.y.z", …}}}
#   absent  -> exit 1, {"error":{"title":"Not Found","status":404, …}}
#
# Approach: a fake `tessl` first on PATH, driven by MOCK_MODE. Real python3
# stays reachable so the classification runs for real.
#
# Run: bash skills/release/tests/test_registry_has_version.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/registry-has-version.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: registry-has-version.sh not found at $SCRIPT" >&2; exit 2; }

TESTTMP="$(mktemp -d)" || { echo "fatal: mktemp -d failed" >&2; exit 2; }
cleanup_tmp() {
  if [[ -n "${TESTTMP:-}" ]]; then
    if ! rm -rf "$TESTTMP"; then
      echo "warning: could not remove temp dir ${TESTTMP} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT

FAIL_COUNT=0
PASS_COUNT=0
run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

STUBDIR="$TESTTMP/stub"
mkdir -p "$STUBDIR" || { echo "fatal: cannot create $STUBDIR" >&2; exit 2; }

# Fake tessl. Every mode also writes tessl's real CLI-update notice to stderr,
# which rides on every live call — the script must keep that out of the body it
# parses.
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
set -uo pipefail
echo 'Latest available version is 0.98.0 (currently running 0.97.0), upgrade with "tessl cli update"' >&2
want="${2##*/versions/}"
case "${MOCK_MODE:-}" in
  present)  printf '{"data":{"attributes":{"version":"%s","moderationStatus":"pass"}}}\n' "$want"; exit 0 ;;
  absent)   echo '{"error":{"title":"Not Found","status":404,"message":"Not Found"}}'; exit 1 ;;
  auth)     echo '{"error":{"title":"Unauthorized","status":401,"message":"Unauthorized"}}'; exit 1 ;;
  garbage)  echo '<html><title>502 Bad Gateway</title></html>'; exit 1 ;;
  mismatch) echo '{"data":{"attributes":{"version":"9.9.9"}}}'; exit 0 ;;
  empty)    exit 1 ;;
  *) echo "stub tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
esac
STUB
chmod +x "$STUBDIR/tessl" || { echo "fatal: chmod stub tessl failed" >&2; exit 2; }
export PATH="$STUBDIR:$PATH"

_run() { MOCK_MODE="$1" bash "$SCRIPT" testws testplugin 1.2.3; }

t_present_is_exists_true() {
  local out rc=0
  out="$(_run present 2>/dev/null)" || rc=$?
  [[ $rc -eq 0 ]] || { echo "    FAIL: expected exit 0, got ${rc}" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["exists"] is True and d["version"]=="1.2.3", d' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
}

# A 404 is an ANSWER, not a failure: the version is genuinely not published.
t_404_is_exists_false_not_an_error() {
  local out rc=0
  out="$(_run absent 2>/dev/null)" || rc=$?
  [[ $rc -eq 0 ]] || { echo "    FAIL: expected exit 0 for a definitive absent, got ${rc}" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["exists"] is False, d' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
}

# An auth failure exits non-zero just like a 404 — collapsing the two would
# report "not published" for a registry we simply could not read.
t_auth_failure_is_indeterminate() {
  local out rc=0
  out="$(_run auth 2>/dev/null)" || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2 for a non-404 failure, got ${rc}" >&2; return 1; }
  [[ -z "$out" ]] || { echo "    FAIL: exit 2 must emit no JSON, got: $out" >&2; return 1; }
}

t_non_json_body_is_indeterminate() {
  local rc=0
  _run garbage >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2 for a non-JSON body, got ${rc}" >&2; return 1; }
}

t_empty_body_is_indeterminate() {
  local rc=0
  _run empty >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2 for an empty body, got ${rc}" >&2; return 1; }
}

# A 200 whose version is NOT the one asked for did not answer this question —
# claiming "exists" there would let a wrong artifact read as landed.
t_version_mismatch_is_indeterminate() {
  local out rc=0
  out="$(_run mismatch 2>/dev/null)" || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2 for a mismatched version, got ${rc}" >&2; return 1; }
  [[ -z "$out" ]] || { echo "    FAIL: exit 2 must emit no JSON, got: $out" >&2; return 1; }
}

t_diagnostic_names_the_cause() {
  local err
  err="$( { _run auth >/dev/null; } 2>&1 )"
  case "$err" in
    *"cannot determine"*"401"*) return 0 ;;
    *) echo "    FAIL: stderr should name the indeterminate cause, got: $err" >&2; return 1 ;;
  esac
}

# <version> is a caller-supplied argument. Interpolating it into a JSON string
# with printf emits INVALID JSON for one carrying a quote or a backslash, which
# breaks the contract this script declares (rules/script-delegation.md —
# JSON-producing). The absent path exercises the serializer without needing the
# stub to produce a matching hostile body.
t_hostile_version_still_emits_valid_json() {
  local out rc=0
  local hostile='1.0.0"\\ evil'
  out="$(MOCK_MODE=absent bash "$SCRIPT" testws testplugin "$hostile" 2>/dev/null)" || rc=$?
  [[ $rc -eq 0 ]] || { echo "    FAIL: expected exit 0, got ${rc}" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["exists"] is False and d["version"]==sys.argv[1], d' "$hostile" \
    || { echo "    FAIL: hostile version broke the JSON contract: $out" >&2; return 1; }
}

t_wrong_arity_is_usage_error() {
  local rc=0
  MOCK_MODE=present bash "$SCRIPT" testws testplugin >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2, got ${rc}" >&2; return 1; }
}

t_empty_version_is_usage_error() {
  local rc=0
  MOCK_MODE=present bash "$SCRIPT" testws testplugin "" >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 2 ]] || { echo "    FAIL: expected exit 2, got ${rc}" >&2; return 1; }
}

echo "== registry-has-version.sh tests =="
run "a published version reads exists=true"              t_present_is_exists_true
run "a 404 reads exists=false (an answer, not a fault)"  t_404_is_exists_false_not_an_error
run "a non-404 failure is indeterminate (exit 2)"        t_auth_failure_is_indeterminate
run "a non-JSON body is indeterminate"                   t_non_json_body_is_indeterminate
run "an empty body is indeterminate"                     t_empty_body_is_indeterminate
run "a mismatched version is indeterminate"              t_version_mismatch_is_indeterminate
run "the indeterminate diagnostic names the cause"       t_diagnostic_names_the_cause
run "a hostile version still emits valid JSON"           t_hostile_version_still_emits_valid_json
run "wrong arity is a usage error (exit 2)"              t_wrong_arity_is_usage_error
run "an empty version argument is a usage error"         t_empty_version_is_usage_error

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
