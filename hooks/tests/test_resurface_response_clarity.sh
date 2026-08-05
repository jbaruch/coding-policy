#!/usr/bin/env bash
# Outcome-based tests for resurface-response-clarity.sh.
#
# Covers the behaviors the hook promises:
#   1. Turn 1 fires — emits {"additionalContext": ...} containing the reminder.
#   2. Turns 2..N stay silent (empty stdout).
#   3. Turn N+1 fires again (every-Nth cadence, first fire on turn 1).
#   4. Exit code is always 0 — never 2 (a block would drop the user's prompt).
#   5. Missing session_id => session "default", still counts and fires.
#   6. Malformed (non-JSON) stdin => no crash, exit 0, treated as "default".
#   7. Corrupt state file => treated as count 0, next turn fires.
#   8. RESURFACE_INTERVAL override respected (N=2 fires on 1,3,5).
#   9. Emitted output is valid JSON with exactly the additionalContext key.
#  10. Determinism — a fixed session's fire pattern is identical across runs.
#
# Black-box approach: the hook is a stdin->stdout filter, so each case pipes a
# JSON payload to `bash <script>` and inspects stdout + exit code. State is
# isolated via RESURFACE_STATE_DIR (a temp dir) plus a distinct session_id per
# case, so counts never bleed between tests.
#
# Run: bash hooks/tests/test_resurface_response_clarity.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/resurface-response-clarity.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: hook not found/readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq is required to run these tests" >&2; exit 2; }

STATE_DIR="$(mktemp -d -t resurface-test.XXXXXX)" || { echo "fatal: mktemp failed for state dir" >&2; exit 2; }
export RESURFACE_STATE_DIR="$STATE_DIR"

cleanup() {
  if [[ -n "${STATE_DIR:-}" ]] && ! rm -rf "$STATE_DIR"; then
    echo "warning: could not remove temp dir ${STATE_DIR} — remove it by hand" >&2
  fi
  return 0
}
trap cleanup EXIT

FAIL_COUNT=0
PASS_COUNT=0

# run <stdin-json> -> sets OUT (stdout) and RC (exit code)
run() {
  OUT="$(printf '%s' "$1" | bash "$SCRIPT")"
  RC=$?
}

payload() { printf '{"session_id":"%s","hook_event_name":"UserPromptSubmit","cwd":"/x","prompt":"hi"}' "$1"; }

# seed_turn <session> <turn> — pre-write state so the next hook call for that
# session lands on <turn>. Keeps each cadence check independent (its own
# session + seeded state), so no check depends on another having run first
# (rules/error-handling.md aggregate carve-out precondition 1; testing-standards
# Independence).
seed_turn() {
  if ! printf '{"schema_version":1,"session_id":"%s","turn_count":%d}' "$1" "$(( $2 - 1 ))" > "${STATE_DIR}/$1.json"; then
    echo "fatal: seed_turn write failed for session $1" >&2
    exit 2
  fi
}

pass() { PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  ✗ FAIL: $1" >&2; }

check_fires() { # $1=label $2=stdin
  run "$2"
  if [[ $RC -ne 0 ]]; then fail "$1: expected exit 0, got $RC"; return; fi
  if ! printf '%s' "$OUT" | jq -e '.additionalContext | test("response-clarity")' >/dev/null 2>&1; then
    fail "$1: expected additionalContext mentioning response-clarity, got: ${OUT}"; return
  fi
  pass
}

check_silent() { # $1=label $2=stdin
  run "$2"
  if [[ $RC -ne 0 ]]; then fail "$1: expected exit 0, got $RC"; return; fi
  if [[ -n "$OUT" ]]; then fail "$1: expected empty stdout, got: ${OUT}"; return; fi
  pass
}

echo "Testing resurface-response-clarity.sh" >&2

# 1-3: default cadence (N=5) — fire on turn 1 and 6, silent on 2..5. Each check
# uses its own session with seeded state, so the checks are order-independent.
unset RESURFACE_INTERVAL
check_fires  "turn 1 fires"                          "$(payload cad1)"
seed_turn cad2 2; check_silent "turn 2 silent"       "$(payload cad2)"
seed_turn cad3 3; check_silent "turn 3 silent"       "$(payload cad3)"
seed_turn cad5 5; check_silent "turn 5 silent"       "$(payload cad5)"
seed_turn cad6 6; check_fires  "turn 6 fires again"  "$(payload cad6)"

# 5: missing session_id — still fires on its first turn.
check_fires "missing session_id fires" '{"hook_event_name":"UserPromptSubmit","cwd":"/x","prompt":"hi"}'

# 6: malformed stdin — no crash, exit 0, fires (treated as default session).
#    Own state dir so the earlier default-session turn doesn't count. The
#    subshell uses local names (sub_out/sub_rc) to stay clear of outer OUT/RC.
if (
  bad_dir="$(mktemp -d -t resurface-bad.XXXXXX)" || { echo "  ✗ malformed stdin: mktemp failed" >&2; exit 1; }
  export RESURFACE_STATE_DIR="$bad_dir"
  sub_out="$(printf '%s' 'not json at all' | bash "$SCRIPT")"; sub_rc=$?
  rm -rf "$RESURFACE_STATE_DIR"
  [[ $sub_rc -eq 0 ]] || { echo "  ✗ malformed stdin: expected exit 0, got $sub_rc" >&2; exit 1; }
  printf '%s' "$sub_out" | jq -e '.additionalContext' >/dev/null 2>&1 || { echo "  ✗ malformed stdin: expected a fire" >&2; exit 1; }
); then pass; else fail "malformed stdin handling"; fi

# 7: corrupt state file — treated as count 0, so the next turn is turn 1 and fires.
printf 'garbage{not json' > "${STATE_DIR}/s_corrupt.json"
check_fires "corrupt state fires" "$(payload s_corrupt)"

# 7b: unrecognized schema_version — no usable prior state (count 0) despite a
#     high turn_count, so the next turn is turn 1 and fires (Migration Policy).
printf '{"schema_version":999,"session_id":"s_ver","turn_count":42}' > "${STATE_DIR}/s_ver.json"
check_fires "unknown schema_version ignores turn_count and fires" "$(payload s_ver)"

# 7c: parseable but octal-looking count ("08") — read base-10 (=8), no crash;
#     next turn is 9, not a fire turn (N=5), so silent (proves no octal abort).
printf '{"schema_version":1,"session_id":"s_oct","turn_count":"08"}' > "${STATE_DIR}/s_oct.json"
check_silent "octal-looking count is base-10 and does not crash" "$(payload s_oct)"

# 8: interval override N=2 — fires on odd turns. Independent seeded state per check.
export RESURFACE_INTERVAL=2
check_fires  "N=2 turn 1 fires"                     "$(payload n2t1)"
seed_turn n2t2 2; check_silent "N=2 turn 2 silent"  "$(payload n2t2)"
seed_turn n2t3 3; check_fires  "N=2 turn 3 fires"   "$(payload n2t3)"
seed_turn n2t4 4; check_silent "N=2 turn 4 silent"  "$(payload n2t4)"
seed_turn n2t5 5; check_fires  "N=2 turn 5 fires"   "$(payload n2t5)"
unset RESURFACE_INTERVAL

# 8b: invalid RESURFACE_INTERVAL — never aborts (exit 0), defaults to 5, fires on turn 1.
export RESURFACE_INTERVAL=not-a-number
check_fires "invalid interval defaults and fires" "$(payload s_badN)"
unset RESURFACE_INTERVAL

# 9: emitted output is valid JSON with exactly one key (additionalContext).
run "$(payload s_shape)"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e 'keys == ["additionalContext"]' >/dev/null 2>&1; then
  pass
else
  fail "output shape: expected single-key additionalContext JSON, got: ${OUT}"
fi

# 10: determinism — two independent sessions fed the same turn sequence
#     produce identical fire/silent patterns.
det() { # emits F/S per turn for a fresh session in a fresh state dir
  local dir sess out pat=""
  dir="$(mktemp -d -t resurface-det.XXXXXX)" || { echo "  ✗ FAIL: det: mktemp failed" >&2; return 1; }
  sess="$1"
  for _ in 1 2 3 4 5 6; do
    out="$(printf '{"session_id":"%s","prompt":"hi"}' "$sess" | RESURFACE_STATE_DIR="$dir" bash "$SCRIPT")"
    if [[ -n "$out" ]]; then pat+="F"; else pat+="S"; fi
  done
  rm -rf "$dir"
  printf '%s' "$pat"
}
P1="$(det da)"; P2="$(det db)"
if [[ "$P1" == "FSSSSF" && "$P1" == "$P2" ]]; then
  pass
else
  fail "determinism: expected FSSSSF twice, got '${P1}' and '${P2}'"
fi

echo "─────────────────────────────────────────────" >&2
if [[ $FAIL_COUNT -gt 0 ]]; then
  echo "FAILED: ${FAIL_COUNT} failed, ${PASS_COUNT} passed" >&2
  exit 1
fi
echo "PASSED: all ${PASS_COUNT} checks" >&2
