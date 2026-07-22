#!/usr/bin/env bash
# Outcome-based tests for review-skills.sh's credit-outage classification —
# the fail-safe logic issue #188 asks the skill-review action to own: under
# CREDIT_OUTAGE=skip, ONLY a tessl "out of credits" (403) failure is
# tolerated (the skill publishes unreviewed, recorded in UNREVIEWED); every
# other non-zero exit still hard-fails, and CREDIT_OUTAGE=fail never
# tolerates anything.
#
# Approach: source review-skills.sh (its main() guard prevents auto-run when
# sourced) and drive its functions directly with a mocked `tessl` shell
# function — a command substitution inherits the caller's functions, so the
# mock intercepts the real call without touching PATH. Coverage:
#   - run_reviews: the credit-outage classification (fail vs skip, the
#     credit-phrase-plus-403 signature, non-credit hard-fail, mixed runs,
#     deleted skills, input validation);
#   - identify_skills: review-all fallback, git-diff changed-skill detection
#     against a throwaway git tree, and unreachable-base hard-fail;
#   - main: the unreviewed-skills output emission.
#
# Determinism (rules/testing-standards.md): assertions never check the
# credit-reset date credit_skip() computes from the wall clock — only that a
# skill was skipped/recorded/failed. Fixtures are built in setup, no binary
# or time-relative inputs.
#
# Run: bash .github/actions/skill-review/tests/test_review_skills.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/review-skills.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: review-skills.sh not found at $SCRIPT" >&2; exit 2; }

# Sourcing runs the script's own `set -euo pipefail`; `|| true` then `set +e`
# restores the harness's discipline (rules/error-handling.md source carve-out).
# The config vars below are `export`ed, mirroring how the action passes them to
# review-skills.sh (via `env:`) — which also tells shellcheck they're used
# externally by the sourced functions, not dead (SC2034).
# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

# Explicit setup checks under `set +e`: a failed mktemp/mkdir would otherwise
# leave a bad fixture path and the assertions below would no longer mean what
# they report (rules/error-handling.md setup-failure clause).
FIXTURE=$(mktemp -d -t skill-review-test.XXXXXX) \
  || { echo "fatal: could not create fixture dir" >&2; exit 2; }
cleanup_tmp() {
  if [[ -n "${FIXTURE:-}" ]]; then
    if ! rm -rf "$FIXTURE"; then
      echo "warning: could not remove temp dir $FIXTURE — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT

# Fixture skills. `needs-credits` is named so the by-path mock can single it
# out for a mixed run; the rest are plain.
for s in alpha beta needs-credits; do
  mkdir -p "$FIXTURE/$s" || { echo "fatal: could not create fixture $s" >&2; exit 2; }
  : > "$FIXTURE/$s/SKILL.md" || { echo "fatal: could not write fixture $s/SKILL.md" >&2; exit 2; }
done
# A skill dir whose SKILL.md is absent (a deleted skill) — run_reviews must
# skip it, never call tessl on it.
mkdir -p "$FIXTURE/deleted" || { echo "fatal: could not create fixture deleted" >&2; exit 2; }

# Global mock. MOCK_MODE selects behaviour; the last arg is the SKILL.md
# path. tessl runs inside run_reviews' command substitution (a subshell), so
# the call count is logged to a file rather than a variable — a subshell's
# variable writes never reach the parent.
MOCK_MODE="success"
MOCK_CALLS_FILE="$FIXTURE/tessl-calls.log"
# Invoked only from run_reviews inside the sourced review-skills.sh (via a
# command substitution shellcheck cannot trace), never directly here.
# shellcheck disable=SC2329
tessl() {
  echo x >> "$MOCK_CALLS_FILE"
  local path="${!#}"
  case "$MOCK_MODE" in
    success) return 0 ;;
    credits)
      echo "✘ 403 Forbidden"
      echo "Your organization has run out of credits. Upgrade your plan or buy more credits to continue."
      return 1
      ;;
    threshold)
      echo "Skill scored 70 — below threshold 85."
      return 1
      ;;
    phrase-only)
      # The EXACT production-matched phrase ("run out of credits") but no
      # 403 and no full billing sentence — must NOT be classed as an
      # outage. Using the exact phrase is the point: if is_credit_outage
      # ever loosened to the phrase alone, this case would flip to a
      # wrongful skip and fail.
      echo "Your organization has run out of credits."
      return 1
      ;;
    billing-sentence)
      # The current tessl CLI's billing failure verbatim — no 403 anywhere
      # in the output (nanoclaw-admin run 29951572295, 2026-07-22). The
      # whole-line sentence signature must classify this as an outage,
      # tolerating the leading "✘ " glyph.
      echo "- Creating review run..."
      echo "✖ Failed to create review run"
      echo "✘ Your organization has run out of credits. Upgrade your plan or buy more credits to continue."
      return 1
      ;;
    prose-quote)
      # A real review failure whose feedback QUOTES the full billing
      # sentence mid-line — must NOT be classed as an outage: the
      # signature is line-anchored, and quoted text has surrounding
      # prose on the same line.
      echo "Skill scored 60 — below threshold 85."
      echo "Feedback: the error message 'Your organization has run out of credits. Upgrade your plan or buy more credits to continue.' should not be hardcoded in the skill body."
      return 1
      ;;
    code-only)
      # A 403 without the credit phrase — a different auth/forbidden error.
      echo "✘ 403 Forbidden — token lacks scope for this workspace."
      return 1
      ;;
    by-path)
      if [[ "$path" == *needs-credits* ]]; then
        echo "✘ 403 Forbidden"
        echo "Your organization has run out of credits."
        return 1
      fi
      return 0
      ;;
    *) echo "mock: unknown MOCK_MODE '$MOCK_MODE'" >&2; return 99 ;;
  esac
}

# Drive run_reviews with the harness globals set, capturing rc, the emitted
# output (stdout+stderr, where ::warning:: lands), and the resulting
# UNREVIEWED array. run_reviews runs in THIS shell (output redirected to a
# file, not command-substituted) so its UNREVIEWED writes survive. Sinks are
# fixture files so credit_skip's GITHUB_STEP_SUMMARY note is inspectable.
drive() {
  local mode="$1"; shift          # CREDIT_OUTAGE value
  : > "$MOCK_CALLS_FILE"
  export SKILLS_DIR="$FIXTURE" THRESHOLD="85" CREDIT_OUTAGE="$mode"
  export GITHUB_STEP_SUMMARY="$FIXTURE/summary.txt"; : > "$GITHUB_STEP_SUMMARY"
  UNREVIEWED=()
  run_reviews "$@" > "$FIXTURE/run.out" 2>&1
  DRIVE_RC=$?
  DRIVE_OUT="$(cat "$FIXTURE/run.out")"
  MOCK_CALLS=$(wc -l < "$MOCK_CALLS_FILE" | tr -d ' ')
}

assert_rc() {
  local want="$1" name="$2"
  if [[ "$DRIVE_RC" -eq "$want" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $name — expected rc $want, got $DRIVE_RC" >&2
    echo "  output: $DRIVE_OUT" >&2
  fi
}

assert_unreviewed() {
  local want="$1" name="$2"
  local got; got=$(IFS=,; printf '%s' "${UNREVIEWED[*]:-}")
  if [[ "$got" == "$want" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $name — expected UNREVIEWED='$want', got '$got'" >&2
  fi
}

assert_contains() {
  local hay="$1" needle="$2" name="$3"
  if [[ "$hay" == *"$needle"* ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $name — expected to contain '$needle'" >&2
    echo "  in: $hay" >&2
  fi
}

assert_eq() {
  local got="$1" want="$2" name="$3"
  if [[ "$got" == "$want" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $name — expected '$want', got '$got'" >&2
  fi
}

# --- fail mode: nothing is tolerated ---

MOCK_MODE="success"; drive fail alpha
assert_rc 0 "fail+success: clean review passes"
assert_unreviewed "" "fail+success: nothing unreviewed"

MOCK_MODE="credits"; drive fail alpha
assert_rc 1 "fail+credits: credit outage still hard-fails under fail mode"
assert_unreviewed "" "fail+credits: nothing recorded as unreviewed"

# --- skip mode: only the credit signature is tolerated ---

MOCK_MODE="credits"; drive skip alpha
assert_rc 0 "skip+credits: credit outage tolerated"
assert_unreviewed "alpha" "skip+credits: skill recorded unreviewed"
assert_contains "$DRIVE_OUT" "::warning::" "skip+credits: emits a warning"
assert_contains "$DRIVE_OUT" "out of credits" "skip+credits: warning names the cause"
assert_contains "$(cat "$FIXTURE/summary.txt")" "not** reviewed" "skip+credits: writes a run-summary note"

MOCK_MODE="threshold"; drive skip alpha
assert_rc 1 "skip+threshold: a real below-threshold failure still hard-fails"
assert_unreviewed "" "skip+threshold: not recorded as a credit skip"
assert_contains "$DRIVE_OUT" "not a credits outage" "skip+threshold: diagnostic distinguishes the cause"

# Detection precision (issue #188 follow-up): skip requires the legacy
# phrase-plus-403 signature OR the full current-CLI billing sentence.
# A partial phrase alone, or a 403 alone, is a real failure and must
# hard-fail.
MOCK_MODE="phrase-only"; drive skip alpha
assert_rc 1 "skip+phrase-without-403: partial credit phrase alone is not an outage — hard-fails"
assert_unreviewed "" "skip+phrase-without-403: not recorded as a credit skip"

MOCK_MODE="code-only"; drive skip alpha
assert_rc 1 "skip+403-without-phrase: a different 403 is not an outage — hard-fails"
assert_unreviewed "" "skip+403-without-phrase: not recorded as a credit skip"

# Signature drift (the 2026-07-22 fleet-wide publish block): the current
# tessl CLI emits the billing sentence with no 403. The full sentence must
# classify as an outage under skip — and still hard-fail under fail mode.
MOCK_MODE="billing-sentence"; drive skip alpha
assert_rc 0 "skip+billing-sentence: current-CLI outage (no 403) tolerated"
assert_unreviewed "alpha" "skip+billing-sentence: skill recorded unreviewed"
assert_contains "$DRIVE_OUT" "::warning::" "skip+billing-sentence: emits a warning"

MOCK_MODE="billing-sentence"; drive fail alpha
assert_rc 1 "fail+billing-sentence: outage still hard-fails under fail mode"
assert_unreviewed "" "fail+billing-sentence: nothing recorded as unreviewed"

# Line anchoring: a real failure quoting the full sentence mid-line must
# not skip — the signature only matches the sentence as an entire line.
MOCK_MODE="prose-quote"; drive skip alpha
assert_rc 1 "skip+prose-quote: quoted billing sentence mid-line is not an outage — hard-fails"
assert_unreviewed "" "skip+prose-quote: not recorded as a credit skip"

MOCK_MODE="success"; drive skip alpha beta
assert_rc 0 "skip+success: all clean passes"
assert_unreviewed "" "skip+success: nothing unreviewed"

# --- mixed: one credit outage among clean reviews (skip mode) ---

MOCK_MODE="by-path"; drive skip alpha needs-credits beta
assert_rc 0 "skip+mixed: run continues past the credit skip"
assert_unreviewed "needs-credits" "skip+mixed: only the outage skill is recorded"

# --- deleted skill (no SKILL.md) is skipped, never reviewed ---

MOCK_MODE="success"; drive skip deleted
assert_rc 0 "deleted skill: skipped, no failure"
assert_eq "$MOCK_CALLS" "0" "deleted skill: tessl never invoked"
assert_unreviewed "" "deleted skill: nothing unreviewed"

# --- input validation: an unknown mode is a setup error, not a silent fail ---

MOCK_MODE="success"; drive bogus alpha
assert_rc 2 "invalid credit-outage: rejected with exit 2"
assert_contains "$DRIVE_OUT" "invalid credit-outage" "invalid credit-outage: names the bad value"

# --- identify_skills: changed-skill detection, review-all fallback, base failure ---

# Run identify_skills with the given event context, echoing its output. The
# config are `export`ed — the env contract review-skills.sh reads, and read
# as used-externally so the linter does not flag them dead. Callers invoke
# this inside a `$()` capture, so both the exports and the `cd` stay confined
# to that subshell.
capture_identify() {
  local dir="$1"
  export SKILLS_DIR="$2" BASE_OVERRIDE="$3" EVENT_NAME="$4" EVENT_BEFORE="$5"
  cd "$dir" || return 9
  identify_skills
}

# Review-all fallback (workflow_dispatch / no base) returns every immediate
# skill-dir basename, sorted. FIXTURE holds alpha, beta, deleted, needs-credits.
got=$(capture_identify "$FIXTURE" "$FIXTURE" "" "workflow_dispatch" "")
assert_eq "$got" "$(printf 'alpha\nbeta\ndeleted\nneeds-credits')" "identify_skills: review-all lists every skill dir, sorted"

# A throwaway git tree with a base commit and a HEAD that modifies alpha and
# adds beta — the diff base is HEAD~1, so both count as changed.
GITREPO="$FIXTURE/gitrepo"
make_git_fixture() {
  rm -rf "$GITREPO"
  mkdir -p "$GITREPO" || return 1
  (
    set -e
    cd "$GITREPO"
    git init -q
    git config user.email t@e.test
    git config user.name tester
    mkdir -p skills/alpha; echo a > skills/alpha/SKILL.md
    git add -A; git commit -q -m base
    mkdir -p skills/beta; echo b > skills/beta/SKILL.md
    echo more >> skills/alpha/SKILL.md
    git add -A; git commit -q -m head
  )
}
make_git_fixture || { echo "fatal: could not build git fixture" >&2; exit 2; }
base_sha=$( cd "$GITREPO" && git rev-parse HEAD~1 )

got=$(capture_identify "$GITREPO" "skills" "" "push" "$base_sha")
assert_eq "$got" "$(printf 'alpha\nbeta')" "identify_skills: git-diff detects the modified and added skills"

# An unreachable (non-sentinel) base with no remote to fetch from must
# hard-fail, never collapse to "no changes".
rc=0
capture_identify "$GITREPO" "skills" "" "push" "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "2" "identify_skills: unreachable base hard-fails with exit 2"

# --- main: emits the unreviewed-skills output ---

# Run main with the given context; its GITHUB_OUTPUT write is the artifact
# under test. Config are `export`ed like capture_identify's, but this runs in
# the current shell (not a `$()`), so the exports persist afterward — harmless
# here: these are the final cases and each overwrites the values it needs.
capture_main() {
  export SKILLS_DIR="$1" EVENT_NAME="$2" EVENT_BEFORE="$3" CREDIT_OUTAGE="$4" \
         GITHUB_OUTPUT="$5" GITHUB_STEP_SUMMARY="$6" BASE_OVERRIDE="" THRESHOLD="85"
  main
}

# Review-all over the fixture with a per-path mock: needs-credits hits the
# credit outage (skip), alpha/beta pass, deleted has no SKILL.md. main must
# write the outage skill to $GITHUB_OUTPUT.
GH_OUT="$FIXTURE/gh_output"; : > "$GH_OUT"
MOCK_MODE="by-path"
capture_main "$FIXTURE" "workflow_dispatch" "" "skip" "$GH_OUT" "$FIXTURE/sum2" >/dev/null 2>&1
assert_eq "$(cat "$GH_OUT")" "unreviewed-skills=needs-credits" "main: writes the credit-skipped skill to GITHUB_OUTPUT"

# A fully-clean review-all writes an empty output value.
GH_OUT2="$FIXTURE/gh_output2"; : > "$GH_OUT2"
MOCK_MODE="success"
capture_main "$FIXTURE" "workflow_dispatch" "" "skip" "$GH_OUT2" "$FIXTURE/sum3" >/dev/null 2>&1
assert_eq "$(cat "$GH_OUT2")" "unreviewed-skills=" "main: clean review-all writes an empty unreviewed-skills value"

echo "─────────────────────────────────────────────"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "FAILED: $FAIL_COUNT failed, $PASS_COUNT passed." >&2
  exit 1
fi
echo "PASSED: all $PASS_COUNT assertion(s)."
