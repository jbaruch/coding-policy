#!/usr/bin/env bash
# Guard the two SKILL.md conventions a consumer agent depends on.
#
# 1. Every script invocation carries a `bash ` prefix. tessl packaging
#    normalizes plugin files to 0644, so a bare path is a permission-denied on
#    every consumer — and `chmod +x` in this repo does not survive publish.
#    This is a deterministic check because the failure is invisible here: the
#    scripts run fine from a clone and only break once installed.
# 2. The skill gates on HERDR_ENV before any step. A single agent on an
#    isolated task must be turned away by reading, not by running a script and
#    reading its error.
#
# `set -e` is dropped so both checks run and the suite reports an aggregate
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash skills/herdr-teamlead/tests/test_skill_invocations.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "could not resolve the skill dir"
SKILL="${SKILL_DIR}/SKILL.md"
[[ -r "$SKILL" ]] || die "SKILL.md not found at $SKILL"

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# 1. No bare invocation of a plugin script.
# grep exits 1 on no-match (what we want) and 2 on error; branching on the
# status keeps an unreadable SKILL.md from reading as "clean".
grep_rc=0
bare="$(grep -nE '^[[:space:]]*\.tessl/plugins/\S+\.sh' "$SKILL")" || grep_rc=$?
case "$grep_rc" in
  1) pass ;;
  0)
    fail "bare script invocation(s) — tessl ships plugin files 0644, so these are permission-denied on every consumer:"
    printf '%s\n' "$bare" >&2
    ;;
  *) fail "could not scan SKILL.md (grep exit ${grep_rc})" ;;
esac

# 2. Every fenced invocation of a shipped .sh goes through bash.
grep_rc=0
invocations="$(grep -cE '^[[:space:]]*bash \.tessl/plugins/\S+\.sh' "$SKILL")" || grep_rc=$?
if (( grep_rc == 0 )) && (( invocations > 0 )); then pass
else fail "no \`bash \` prefixed invocations found — the convention regressed (grep exit ${grep_rc})"; fi

# 3. The HERDR_ENV gate precedes Step 1.
gate_line="$(grep -n 'HERDR_ENV' "$SKILL" | head -1 | cut -d: -f1)" || true
step1_line="$(grep -n '^## Step 1 ' "$SKILL" | head -1 | cut -d: -f1)" || true
if [[ -z "$gate_line" || -z "$step1_line" ]]; then
  fail "could not locate the HERDR_ENV gate or Step 1 heading"
elif (( gate_line < step1_line )); then pass
else fail "the HERDR_ENV gate (line ${gate_line}) comes after Step 1 (line ${step1_line})"; fi

# 4. The gate tells the agent to stop rather than to run something. The prose
# is hard-wrapped, so the phrase is matched against a whitespace-flattened
# copy rather than line by line.
flat="$(tr '\n' ' ' < "$SKILL" | tr -s '[:space:]' ' ')" || flat=""
if [[ -z "$flat" ]]; then
  fail "could not flatten SKILL.md to check the gate wording"
elif [[ "$flat" == *"this skill does not apply"* ]]; then pass
else fail "the gate does not tell a non-Herdr agent the skill does not apply"; fi

echo
echo "results: ${PASS} pass, ${FAIL} fail"
(( FAIL == 0 ))
