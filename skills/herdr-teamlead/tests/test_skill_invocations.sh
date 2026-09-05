#!/usr/bin/env bash
# Guard the SKILL.md conventions a consumer agent depends on.
#
# Invocation conventions, checked against EVERY skill in SKILLS below:
# 1. Every script invocation carries a `bash ` prefix. tessl packaging
#    normalizes plugin files to 0644, so a bare path is a permission-denied on
#    every consumer, and `chmod +x` in this repo does not survive publish.
#    Deterministic because the failure is invisible here: the scripts run fine
#    from a clone and break only once installed.
# 2. The `bash ` convention is present, so a rewrite that drops every
#    invocation cannot pass check 1 vacuously.
# 3. Every `$CP` use sits in a fenced block that resolved `CP=` first. The
#    plugin root differs between a project-local and a global install, so each
#    block carries its own resolver — an agent's shell state does not survive
#    between tool calls.
#
# Mode-gate conventions, checked against MODE_GATE_SKILL only:
# 4. The first step gates on HERDR_ENV before any script, and the gate turns a
#    standalone agent away by reading rather than by running a script.
#
# `set -e` is dropped so every check runs and the suite reports an aggregate;
# each check captures its own status (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Run: bash skills/herdr-teamlead/tests/test_skill_invocations.sh
set -uo pipefail

# Every skill whose SKILL.md invokes a plugin script. herdr-standup shipped
# bare invocations while this suite resolved its target through its own
# directory, so it only ever read herdr-teamlead's SKILL.md.
SKILLS=(herdr-teamlead herdr-standup)

# herdr-teamlead alone carries the standalone/Herdr mode gate. herdr-standup
# turns a non-Herdr agent away through roster.sh's exit 1, not by reading.
MODE_GATE_SKILL=herdr-teamlead

die() { echo "fatal: $*" >&2; exit 2; }
warn_cleanup() { echo "warn: could not remove $1" >&2; }

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

check_invocations() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # 1. No bare invocation of a plugin script, in either shape — the literal
  # mount path, or a resolved `$CP` with no interpreter in front of it.
  local bare rc=0
  bare="$(grep -nE '^[[:space:]]*"?([$]CP|\.tessl/plugins)/\S+\.sh' "$skill")" || rc=$?
  case "$rc" in
    1) pass ;;
    0)
      fail "${name}: bare script invocation(s) — tessl ships plugin files 0644, so these are permission-denied on every consumer:"
      printf '%s\n' "$bare" >&2
      ;;
    *) die "grep failed scanning ${skill} for bare invocations (exit ${rc})" ;;
  esac

  # 2. The `bash ` convention is actually present, so a future rewrite that
  # drops every invocation cannot pass check 1 vacuously.
  local invocations
  rc=0
  invocations="$(grep -cE '^[[:space:]]*bash "[$]CP/\S+\.sh"' "$skill")" || rc=$?
  case "$rc" in
    0) if (( invocations > 0 )); then pass; else fail "${name}: no bash-prefixed invocations found"; fi ;;
    1) fail "${name}: no bash-prefixed invocations found — the convention regressed" ;;
    *) die "grep failed counting invocations in ${skill} (exit ${rc})" ;;
  esac

  # 3. Every fenced block that uses `$CP` defines it first. A block that
  # inherits the resolver from an earlier block is broken on arrival: the
  # agent runs each block as its own tool call, in a fresh shell.
  local unresolved
  rc=0
  unresolved="$(awk '
    /^[[:space:]]*```/ { inblock = !inblock; resolved = 0; next }
    !inblock { next }
    /^[[:space:]]*CP=/ { resolved = 1; next }
    index($0, "$CP") && !resolved { print FNR ": " $0 }
  ' "$skill")" || rc=$?
  if (( rc != 0 )); then
    die "awk failed scanning ${skill} for unresolved \$CP uses (exit ${rc})"
  fi
  if [[ -z "$unresolved" ]]; then
    pass
  else
    fail "${name}: \$CP used in a block that never resolved it — each block is its own shell:"
    printf '%s\n' "$unresolved" >&2
  fi
}

check_mode_gate() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # Checks 3 and 4 read the BODY only. Matching the whole file would find
  # HERDR_ENV in the frontmatter `description`, so deleting the entire gate
  # section would still pass -- an assertion that survives the removal of the
  # thing it asserts is not a test (rules/testing-standards.md Assertions).
  local body
  body="$(awk 'BEGIN{n=0} /^---[[:space:]]*$/{n++; next} n>=2' "$skill")" \
    || die "could not strip the frontmatter from ${skill}"
  [[ -n "$body" ]] || die "${skill} has no body after its frontmatter"

  local body_file="${TMPDIR:-/tmp}/skill-body.$$.md"
  printf '%s\n' "$body" > "$body_file" || die "could not stage the skill body"

  # 4a. The mode gate is the first step, before roster/script execution.
  local gate_line step1_line step2_line
  gate_line="$(first_match_line 'HERDR_ENV' "$body_file")"
  step1_line="$(first_match_line '^## Step 1 ' "$body_file")"
  step2_line="$(first_match_line '^## Step 2 ' "$body_file")"
  if [[ -z "$gate_line" ]]; then
    fail "${name}: the body states no HERDR_ENV gate — the frontmatter alone does not gate execution"
  elif [[ -z "$step1_line" ]]; then
    fail "${name}: could not locate the Step 1 heading in the body"
  elif [[ -z "$step2_line" ]]; then
    fail "${name}: could not locate the Step 2 heading in the body"
  elif (( step1_line < gate_line && gate_line < step2_line )); then pass
  else fail "${name}: the HERDR_ENV gate (body line ${gate_line}) is outside Step 1"; fi

  # 4b. That gate tells a standalone agent to stop, and says so before the
  # next step rather than anywhere in the file.
  local head flat
  if [[ -n "$step1_line" && -n "$step2_line" ]]; then
    head="$(sed -n "${step1_line},${step2_line}p" "$body_file")" || die "could not read the mode gate"
  else
    head=""
  fi
  flat="$(printf '%s' "$head" | tr '\n' ' ' | tr -s '[:space:]' ' ' \
    | tr '[:upper:]' '[:lower:]')" || die "could not flatten the preamble"
  if [[ "$flat" == *"this skill does not apply"* ]]; then pass
  else fail "${name}: Step 1 does not tell a non-Herdr agent the skill does not apply"; fi

  rm -f "$body_file" || warn_cleanup "$body_file"
}

main() {
  local skills_root skill name
  skills_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || die "could not resolve the skills dir"

  for name in "${SKILLS[@]}"; do
    skill="${skills_root}/${name}/SKILL.md"
    [[ -r "$skill" ]] || die "SKILL.md not found at ${skill}"
    check_invocations "$name" "$skill"
  done

  skill="${skills_root}/${MODE_GATE_SKILL}/SKILL.md"
  check_mode_gate "$MODE_GATE_SKILL" "$skill"

  echo
  echo "results: ${PASS} pass, ${FAIL} fail"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
