#!/usr/bin/env bash
# Guard the two SKILL.md conventions a consumer agent depends on.
#
# 1. Every script invocation carries a `bash ` prefix. tessl packaging
#    normalizes plugin files to 0644, so a bare path is a permission-denied on
#    every consumer, and `chmod +x` in this repo does not survive publish.
#    Deterministic because the failure is invisible here: the scripts run fine
#    from a clone and break only once installed.
# 2. The skill gates on HERDR_ENV before any step, and the gate turns a
#    standalone agent away by reading rather than by running a script.
#
# `set -e` is dropped so every check runs and the suite reports an aggregate;
# each check captures its own status (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Run: bash skills/herdr-teamlead/tests/test_skill_invocations.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Echo the first matching line number, or "" when there is no match. Exits the
# suite on a grep tool error: an unreadable SKILL.md must never read as a
# clean file. grep exits 1 on no-match and 2+ on error, and collapsing the two
# is what `|| true` would do.
first_match_line() { # <pattern> <file>
  local out rc=0
  out="$(grep -n -- "$1" "$2")" || rc=$?
  case "$rc" in
    0) printf '%s' "$out" | head -1 | cut -d: -f1 ;;
    1) printf '' ;;
    *) die "grep failed on $2 (exit ${rc}) while matching ${1}" ;;
  esac
}

main() {
  local skill_dir skill
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "could not resolve the skill dir"
  skill="${skill_dir}/SKILL.md"
  [[ -r "$skill" ]] || die "SKILL.md not found at ${skill}"

  # 1. No bare invocation of a plugin script.
  local bare rc=0
  bare="$(grep -nE '^[[:space:]]*\.tessl/plugins/\S+\.sh' "$skill")" || rc=$?
  case "$rc" in
    1) pass ;;
    0)
      fail "bare script invocation(s) — tessl ships plugin files 0644, so these are permission-denied on every consumer:"
      printf '%s\n' "$bare" >&2
      ;;
    *) die "grep failed scanning ${skill} for bare invocations (exit ${rc})" ;;
  esac

  # 2. The `bash ` convention is actually present, so a future rewrite that
  # drops every invocation cannot pass check 1 vacuously.
  local invocations
  rc=0
  invocations="$(grep -cE '^[[:space:]]*bash \.tessl/plugins/\S+\.sh' "$skill")" || rc=$?
  case "$rc" in
    0) if (( invocations > 0 )); then pass; else fail "no bash-prefixed invocations found"; fi ;;
    1) fail "no bash-prefixed invocations found — the convention regressed" ;;
    *) die "grep failed counting invocations in ${skill} (exit ${rc})" ;;
  esac

  # 3. The HERDR_ENV gate precedes Step 1.
  local gate_line step1_line
  gate_line="$(first_match_line 'HERDR_ENV' "$skill")"
  step1_line="$(first_match_line '^## Step 1 ' "$skill")"
  if [[ -z "$gate_line" || -z "$step1_line" ]]; then
    fail "could not locate the HERDR_ENV gate or the Step 1 heading"
  elif (( gate_line < step1_line )); then pass
  else fail "the HERDR_ENV gate (line ${gate_line}) comes after Step 1 (line ${step1_line})"; fi

  # 4. The gate turns a standalone agent away. The prose is hard-wrapped, so
  # the phrase is matched against a whitespace-flattened copy.
  local flat
  flat="$(tr '\n' ' ' < "$skill" | tr -s '[:space:]' ' ')" || die "could not read ${skill}"
  if [[ "$flat" == *"this skill does not apply"* ]]; then pass
  else fail "the gate does not tell a non-Herdr agent the skill does not apply"; fi

  echo
  echo "results: ${PASS} pass, ${FAIL} fail"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
