#!/usr/bin/env bash
# Outcome-based tests for hooks/session-start.sh.
#
# Each case copies the entry script into a scratch hooks directory beside fake
# hooks written here, and picks them through SESSION_START_HOOKS.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Several hooks report  -> one payload carrying every status, in order,
#      even when the last hook is silent (the tessl last-output-wins case).
#   2. No hook reports       -> no output, exit 0.
#   3. A hook exits non-zero -> its own status line; the others still arrive.
#   4. A hook prints non-JSON -> its own status line; the others still arrive.
#   5. The plugin manifest declares session-start.sh as the only SessionStart hook.
#
# Run: bash hooks/tests/test_session_start.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

fake_hook() { # <name> <body>
  printf '#!/usr/bin/env bash\nset -euo pipefail\n%s\n' "$2" > "$DIR/$1.sh" || die "cannot write fake hook $1"
}

# run <hook names...> -> OUT, RC
run() {
  OUT="$(SESSION_START_HOOKS="$*" bash "$DIR/session-start.sh" </dev/null 2>"$TMP/err")"
  RC=$?
}

context() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["additionalContext"])' <<<"$OUT"; }

main() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the hooks directory"
  command -v python3 >/dev/null || die "python3 required for these tests"
  TMP="$(mktemp -d -t session-start-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  DIR="$TMP/hooks"
  mkdir -p "$DIR" || die "cannot create $DIR"
  cp "$here/session-start.sh" "$DIR/" || die "cannot copy session-start.sh"
  FAIL=0; PASS=0

  fake_hook one "printf '%s\n' '{\"additionalContext\":\"Session-start status — one\"}'"
  fake_hook two "printf '%s\n' '{\"additionalContext\":\"Session-start status — two\"}'"
  fake_hook quiet "exit 0"
  fake_hook broken "exit 3"
  fake_hook noisy "printf 'not json\n'"

  # 1. every status arrives, in order, despite a silent last hook.
  run one two quiet
  if [[ $RC -eq 0 ]] && [[ "$(context)" == $'Session-start status — one\n\nSession-start status — two' ]]; then
    pass; else fail "merge: expected both statuses in order, got RC=$RC OUT=$OUT"; fi

  # 2. nothing to report -> nothing printed.
  run quiet quiet
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "silent: expected no output, got RC=$RC OUT=$OUT"; fi

  # 3. a failing hook is named, and the others still arrive.
  run one broken two
  if [[ $RC -eq 0 ]] && context | grep -q "hook broken exited 3" && context | grep -q "— one" && context | grep -q "— two"; then
    pass; else fail "failed hook: expected a status naming it, got RC=$RC OUT=$OUT"; fi

  # 4. a hook printing non-JSON is named, and the others still arrive.
  run noisy one
  if [[ $RC -eq 0 ]] && context | grep -q "hook noisy printed something other than" && context | grep -q "— one"; then
    pass; else fail "bad output: expected a status naming it, got RC=$RC OUT=$OUT"; fi

  # 5. the manifest routes SessionStart through this script alone.
  if python3 - "$here/../.tessl-plugin/plugin.json" <<'PY'
import json, sys
groups = json.load(open(sys.argv[1]))["hooks"]["SessionStart"]
hooks = [h for g in groups for h in g["hooks"]]
sys.exit(0 if len(hooks) == 1 and hooks[0]["args"] == ["${TESSL_PLUGIN_DIR}/hooks/session-start.sh"] else 1)
PY
  then pass; else fail "manifest: SessionStart must declare session-start.sh alone"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} failed, ${PASS} passed"; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
