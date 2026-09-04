#!/usr/bin/env bash
# Tests for start-judge-worker.sh.
#
# The script's whole job is proving the judge worker came up on the tier its
# plan names. A start that "worked" but left the worker on a default model is
# the failure it exists to catch, so most cases below are banner cases.
#
# `set -e` is dropped so every case runs and the suite reports an aggregate;
# each setup command is checked explicitly and aborts with a fatal diagnostic
# on failure (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Happy path      -> started, banner echoes model + effort, exit 0, JSON.
#   2. No effort       -> a model taking no effort flag omits --effort, exit 0.
#   3. Model missing   -> banner without the model is exit 4, never dispatched.
#   4. Effort missing  -> model echoed, effort not, is exit 4 (the silent reset).
#   5. Start fails     -> exit 3, named as a start failure.
#   6. Pane read fails -> exit 4: started, tier unproven.
#   7. No judge object -> exit 2 with an actionable message.
#   8. Empty model     -> exit 2, never a start with an empty --model.
#   9. Usage           -> wrong argc is exit 2.
#  10. Missing plan    -> unreadable plan file is exit 2.
#  11. Launch argv     -> --model/--effort land after the `--` separator.
#  12. Split lines     -> model on one row and effort on another is NOT a
#                         verified banner: the tier has to be on one line.
#  13. Non-banner line -> both values together on a row that does NOT match
#                         the banner pattern is exit 4, not a verified tier.
#  14. No pattern      -> a plan with no banner_pattern is exit 2.
#  15. Effort prefix   -> a banner reporting `xhigh` does NOT verify a request
#                         for `high`; the comparison is whole-token.
#  16. Model prefix    -> a longer model id containing the requested one does
#                         not verify either.
#  17. Transcript row  -> a prompt quoting the pattern AND both tier tokens
#                         does not verify: the pattern is anchored and only
#                         the first matching line is read.
#  18. Alternation    -> `^A|B` anchors only A; the whole expression must be
#                         anchored, so B on a transcript row is not a banner.
#  19. Codex kind     -> codex gets `-m` and `-c model_reasoning_effort=`,
#                         never Claude's `--model`/`--effort`.
#  20. Unknown kind   -> refused (exit 2) rather than started untiered.
#  21. No set -u abort -> no case aborts on an unbound variable.
#
# Run: bash skills/herdr-teamlead/tests/test_start_judge_worker.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

main() {
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "could not resolve the skill dir"
SUT="${SCRIPT_DIR}/start-judge-worker.sh"
[[ -r "$SUT" ]] || die "start-judge-worker.sh not found at $SUT"

TMP="$(mktemp -d)" || die "could not create a temp dir"
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
trap cleanup EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# The fake herdr records its argv and replays a banner from $FAKE_BANNER.
cat > "${TMP}/herdr" <<'FAKE' || die "could not write the fake herdr"
#!/usr/bin/env bash
# Full `-e`: this fake runs no aggregate checks, so the carve-out that lets a
# harness drop it does not apply. A failed argv-log write must not be reported
# as a successful herdr call.
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_ARGV_LOG}"
case "${1:-} ${2:-}" in
  "agent start") exit "${FAKE_START_RC:-0}" ;;
  "pane read")
    printf '%s\n' "${FAKE_BANNER-Claude Code · claude-fable-5-1 · effort max}"
    exit "${FAKE_READ_RC:-0}"
    ;;
esac
exit 0
FAKE
chmod +x "${TMP}/herdr" || die "could not chmod the fake herdr"
export PATH="${TMP}:${PATH}"
export HERDR_BIN="${TMP}/herdr"

write_plan() { # <path> <judge-json>
  printf '{"schema_version":1,"assignments":{"judge":"judge"},"judge":%s}\n' "$2" > "$1" \
    || die "could not write the plan at $1"
}

run_sut() { # <plan> <pane> [kind] ; sets OUT/ERR/RC
  export FAKE_ARGV_LOG="${TMP}/argv.log"
  : > "$FAKE_ARGV_LOG" || die "could not truncate the argv log"
  if [[ -n "${3:-}" ]]; then
    OUT="$(bash "$SUT" "$1" "$2" "$3" 2>"${TMP}/err")"
  else
    OUT="$(bash "$SUT" "$1" "$2" 2>"${TMP}/err")"
  fi
  RC=$?
  ERR="$(cat "${TMP}/err")"
  return 0
}

check_no_abort() { # <label>
  case "$ERR" in
    *"unbound variable"*) fail "$1: aborted on an unbound variable" ;;
    *) pass ;;
  esac
}

FULL='{"agent":"judge","model":"claude-fable-5-1","effort":"max","banner_pattern":"^Claude Code"}'
NOEFFORT='{"agent":"judge","model":"claude-haiku-4-5","effort":null,"banner_pattern":"^Claude Code"}'

# 1. Happy path.
write_plan "${TMP}/plan.json" "$FULL"
FAKE_BANNER='Claude Code · claude-fable-5-1 · effort max' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 0 )); then pass; else fail "1: expected exit 0, got ${RC} (${ERR})"; fi
if printf '%s' "$OUT" | grep -q '"banner_verified": *true'; then pass; else fail "1: no banner_verified in ${OUT}"; fi
if printf '%s' "$OUT" | grep -q '"effort": *"max"'; then pass; else fail "1: effort missing from ${OUT}"; fi
check_no_abort "1"

# 2. A model that takes no effort flag.
write_plan "${TMP}/plan-noeffort.json" "$NOEFFORT"
FAKE_BANNER='Claude Code · claude-haiku-4-5' run_sut "${TMP}/plan-noeffort.json" "w1:p1"
if (( RC == 0 )); then pass; else fail "2: expected exit 0, got ${RC} (${ERR})"; fi
if printf '%s' "$OUT" | grep -q '"effort": *null'; then pass; else fail "2: expected null effort in ${OUT}"; fi
grep_rc=0
grep -q -- "--effort" "$FAKE_ARGV_LOG" || grep_rc=$?
case "$grep_rc" in
  0) fail "2: passed --effort for a model with none" ;;
  1) pass ;;
  *) fail "2: could not read the argv log (grep exit ${grep_rc})" ;;
esac
check_no_abort "2"

# 3. The banner does not name the model.
FAKE_BANNER='Claude Code · claude-sonnet-5' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "3: expected exit 4, got ${RC}"; fi
if [[ -z "$OUT" ]]; then pass; else fail "3: emitted stdout on an unproven tier: ${OUT}"; fi
check_no_abort "3"

# 4. Model echoed, effort not -- the silent-reset case.
FAKE_BANNER='Claude Code · claude-fable-5-1' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "4: expected exit 4, got ${RC}"; fi
case "$ERR" in *effort*) pass ;; *) fail "4: stderr does not name the effort: ${ERR}" ;; esac
check_no_abort "4"

# 5. The start itself fails.
FAKE_START_RC=7 run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 3 )); then pass; else fail "5: expected exit 3, got ${RC}"; fi
check_no_abort "5"

# 6. The pane cannot be read: started, tier unproven.
FAKE_READ_RC=9 run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "6: expected exit 4, got ${RC}"; fi
check_no_abort "6"

# 7. A plan with no judge object.
printf '{"schema_version":1,"assignments":{"developer":"claude"}}\n' > "${TMP}/plan-nojudge.json" \
  || die "could not write the judgeless plan"
run_sut "${TMP}/plan-nojudge.json" "w1:p1"
if (( RC == 2 )); then pass; else fail "7: expected exit 2, got ${RC}"; fi
case "$ERR" in *judge*) pass ;; *) fail "7: stderr does not name the judge block: ${ERR}" ;; esac
check_no_abort "7"

# 8. An empty model never reaches the command line.
write_plan "${TMP}/plan-nomodel.json" '{"agent":"judge","model":"","effort":"max","banner_pattern":"^Claude Code"}'
run_sut "${TMP}/plan-nomodel.json" "w1:p1"
if (( RC == 2 )); then pass; else fail "8: expected exit 2, got ${RC}"; fi
grep_rc=0
grep -q "agent start" "$FAKE_ARGV_LOG" || grep_rc=$?
case "$grep_rc" in
  0) fail "8: started a worker with no model" ;;
  1) pass ;;
  *) fail "8: could not read the argv log (grep exit ${grep_rc})" ;;
esac
check_no_abort "8"

# 9. Usage.
OUT="$(bash "$SUT" 2>"${TMP}/err")"; RC=$?; ERR="$(cat "${TMP}/err")"
if (( RC == 2 )); then pass; else fail "9: expected exit 2, got ${RC}"; fi
case "$ERR" in *usage*) pass ;; *) fail "9: no usage line: ${ERR}" ;; esac

# 10. An unreadable plan file.
run_sut "${TMP}/does-not-exist.json" "w1:p1"
if (( RC == 2 )); then pass; else fail "10: expected exit 2, got ${RC}"; fi
check_no_abort "10"

# 11. The launch flags land after the `--` separator herdr passes through.
FAKE_BANNER='Claude Code · claude-fable-5-1 · effort max' run_sut "${TMP}/plan.json" "w1:p1"
# grep exits 1 on no-match and 2 on error; `|| true` would read an unreadable
# log as "no start line" (rules/error-handling.md Shell Error Handling).
grep_rc=0
start_line="$(grep "agent start" "$FAKE_ARGV_LOG")" || grep_rc=$?
case "$grep_rc" in
  0)
    case "$start_line" in
      *"-- --model claude-fable-5-1 --effort max"*) pass ;;
      *) fail "11: launch argv is wrong: ${start_line}" ;;
    esac
    ;;
  1) fail "11: no \`agent start\` line in the argv log" ;;
  *) fail "11: could not read the argv log (grep exit ${grep_rc})" ;;
esac

# 12. Model and effort on DIFFERENT rows is not a verified tier: two unrelated
# transcript lines must never add up to a banner.
FAKE_BANNER='Claude Code · claude-fable-5-1
some transcript row mentioning max elsewhere' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "12: split-line banner passed as verified (rc ${RC})"; fi
if [[ -z "$OUT" ]]; then pass; else fail "12: emitted stdout on a split-line banner: ${OUT}"; fi
check_no_abort "12"

# 13. The model and effort together on a NON-banner row is not a verified
# tier: ordinary transcript text must never stand in for the startup banner.
FAKE_BANNER='Claude Code
> tell me about claude-fable-5-1 at max effort' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "13: transcript row passed as a banner (rc ${RC})"; fi
if [[ -z "$OUT" ]]; then pass; else fail "13: emitted stdout on a non-banner match: ${OUT}"; fi
check_no_abort "13"

# 14. A plan carrying no banner pattern cannot prove anything: refuse up front.
write_plan "${TMP}/plan-nopattern.json" '{"agent":"judge","model":"m","effort":"max"}'
run_sut "${TMP}/plan-nopattern.json" "w1:p1"
if (( RC == 2 )); then pass; else fail "14: expected exit 2, got ${RC}"; fi
grep_rc=0
grep -q "agent start" "$FAKE_ARGV_LOG" || grep_rc=$?
case "$grep_rc" in
  0) fail "14: started a worker with no way to verify its tier" ;;
  1) pass ;;
  *) fail "14: could not read the argv log (grep exit ${grep_rc})" ;;
esac
check_no_abort "14"

# 15. `high` is a substring of `xhigh`: a banner reporting a DIFFERENT effort
# than the one requested must not verify.
write_plan "${TMP}/plan-high.json" '{"agent":"judge","model":"claude-fable-5-1","effort":"high","banner_pattern":"^Claude Code"}'
FAKE_BANNER='Claude Code · claude-fable-5-1 · effort xhigh' run_sut "${TMP}/plan-high.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "15: xhigh verified a request for high (rc ${RC})"; fi
check_no_abort "15"

# 16. The same trap on model ids that extend one another.
write_plan "${TMP}/plan-short.json" '{"agent":"judge","model":"claude-fable-5","effort":"max","banner_pattern":"^Claude Code"}'
FAKE_BANNER='Claude Code · claude-fable-5-1 · effort max' run_sut "${TMP}/plan-short.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "16: a longer model id verified a shorter request (rc ${RC})"; fi
check_no_abort "16"

# 15b. The exact effort still verifies.
FAKE_BANNER='Claude Code · claude-fable-5-1 · effort high' run_sut "${TMP}/plan-high.json" "w1:p1"
if (( RC == 0 )); then pass; else fail "15b: an exact effort match was refused (rc ${RC}) ${ERR}"; fi
check_no_abort "15b"

# 17. A transcript row carrying the pattern text and both tier tokens must not
# verify. This is the case an unanchored pattern let through.
FAKE_BANNER='> explain Claude Code claude-fable-5-1 max' run_sut "${TMP}/plan.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "17: a transcript row verified the tier (rc ${RC})"; fi
if [[ -z "$OUT" ]]; then pass; else fail "17: emitted stdout for a transcript row: ${OUT}"; fi
check_no_abort "17"

# 18. `^Claude Code|Codex` anchors only the first branch. The second must not
# match mid-line, or a transcript row becomes tier proof again.
write_plan "${TMP}/plan-alt.json" '{"agent":"judge","model":"claude-fable-5-1","effort":"max","banner_pattern":"^Claude Code|Codex"}'
FAKE_BANNER='> explain Codex claude-fable-5-1 max' run_sut "${TMP}/plan-alt.json" "w1:p1"
if (( RC == 4 )); then pass; else fail "18: an unanchored alternative verified a transcript row (rc ${RC})"; fi
check_no_abort "18"

# 18b. The same pattern still verifies a real banner on its second branch.
FAKE_BANNER='Codex · claude-fable-5-1 · effort max' run_sut "${TMP}/plan-alt.json" "w1:p1"
if (( RC == 0 )); then pass; else fail "18b: an anchored alternative was refused (rc ${RC}) ${ERR}"; fi
check_no_abort "18b"

# 19. Codex spells the tier differently; Claude's flags would start it on its
# default model with none of them applied.
FAKE_BANNER='Codex · claude-fable-5-1 · effort max' run_sut "${TMP}/plan.json" "w1:p1" codex
grep_rc=0
start_line="$(grep "agent start" "$FAKE_ARGV_LOG")" || grep_rc=$?
case "$grep_rc" in
  0)
    case "$start_line" in
      *"-- -m claude-fable-5-1 -c model_reasoning_effort=max"*) pass ;;
      *) fail "19: codex argv is wrong: ${start_line}" ;;
    esac
    ;;
  1) fail "19: no \`agent start\` line for the codex kind" ;;
  *) fail "19: could not read the argv log (grep exit ${grep_rc})" ;;
esac
check_no_abort "19"

# 20. A kind with no known flag spelling is refused, never started untiered.
run_sut "${TMP}/plan.json" "w1:p1" gemini
if (( RC == 2 )); then pass; else fail "20: expected exit 2 for an unknown kind, got ${RC}"; fi
grep_rc=0
grep -q "agent start" "$FAKE_ARGV_LOG" || grep_rc=$?
case "$grep_rc" in
  0) fail "20: started a worker whose tier flags are unknown" ;;
  1) pass ;;
  *) fail "20: could not read the argv log (grep exit ${grep_rc})" ;;
esac
check_no_abort "20"

echo
echo "results: ${PASS} pass, ${FAIL} fail"
(( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
