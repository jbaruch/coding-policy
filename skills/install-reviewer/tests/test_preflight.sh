#!/usr/bin/env bash
# Outcome-based tests for preflight.sh focused on the JSON-contract
# guarantees that callers depend on. Currently covers the missing-jq
# guard (#40): preflight must emit a parseable JSON envelope even when
# jq itself isn't on PATH, since the agent parses our stdout.
#
# Run: bash skills/install-reviewer/tests/test_preflight.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/preflight.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: preflight.sh not executable at $SCRIPT" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Per-test sandbox: same shape as test_branch.sh — fresh git repo
# with bare-repo origin so no shared mutable state.
with_repo() {
  local name="$1"; shift
  local root
  root=$(mktemp -d "/tmp/test_preflight.${name}.XXXXXX") || return 1
  local repo="$root/repo"
  local origin="$root/origin.git"
  (
    set -e
    git -c init.defaultBranch=main init -q "$repo"
    cd "$repo"
    git commit --allow-empty -m init -q
    git init --bare -q "$origin"
    git remote add origin "$origin"
    git push -q origin main
  ) || { rm -rf "$root"; return 1; }
  (
    cd "$repo"
    "$@"
  )
  local rc=$?
  rm -rf "$root"
  return $rc
}

# Find a PATH that excludes jq. macOS ships `/usr/bin/jq` and Linux
# distros usually drop jq in `/usr/bin/jq` too, so a pure-/bin PATH is
# the most portable jq-free environment. We assert it's actually
# jq-free before running the test so the missing-jq case is exercised.
no_jq_path() {
  local path="/bin"
  if PATH="$path" command -v jq >/dev/null 2>&1; then
    echo "    SKIP: cannot construct a jq-free PATH on this system" >&2
    return 1
  fi
  echo "$path"
}

# --- test bodies ---

# Without jq, preflight must hand-roll a structured-JSON failure
# envelope rather than dying with `jq: command not found`. The agent
# parses stdout — anything else means the install workflow stalls
# before the agent can report a recovery command.
t_missing_jq_emits_structured_failure_install_mode() {
  local path
  path=$(no_jq_path) || return 0  # SKIP returns 0 to avoid noisy fail
  # The script-under-test is expected to exit 1 here; this file does not
  # run with errexit (`set -uo pipefail` only), so the failure does not
  # abort the function. Earlier revisions wrapped this in `set +e` /
  # `set -e`, but since errexit was never on, the only effect was
  # globally enabling errexit for every test that ran afterwards —
  # exactly the kind of cross-test state leak rules/testing-standards.md
  # warns against.
  local out rc
  out=$(env -i PATH="$path" HOME="$HOME" "$SCRIPT" 2>/dev/null)
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  # Must be valid JSON
  echo "$out" | env -i PATH="$PATH" jq -e . >/dev/null || { echo "    FAIL: stdout is not valid JSON: $out" >&2; return 1; }
  assert_eq "ok"        "false"          "$(echo "$out" | jq -r .ok)"                                         || return 1
  assert_eq "override"  "false"          "$(echo "$out" | jq -r .override)"                                   || return 1
  assert_eq "check"     "jq-installed"   "$(echo "$out" | jq -r '.failures[0].check')"                        || return 1
  local reason
  reason=$(echo "$out" | jq -r '.failures[0].reason')
  [[ "$reason" == *"jq is not installed"* ]] || { echo "    FAIL: missing 'jq is not installed' in reason: $reason" >&2; return 1; }
  [[ "$reason" == *"brew install jq"* ]]     || { echo "    FAIL: missing 'brew install jq' in reason: $reason" >&2; return 1; }
  [[ "$reason" == *"apt install jq"* ]]      || { echo "    FAIL: missing 'apt install jq' in reason: $reason" >&2; return 1; }
}

t_missing_jq_emits_structured_failure_override_mode() {
  local path
  path=$(no_jq_path) || return 0
  local out rc
  out=$(env -i PATH="$path" HOME="$HOME" "$SCRIPT" --override 2>/dev/null)
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  echo "$out" | env -i PATH="$PATH" jq -e . >/dev/null || { echo "    FAIL: stdout is not valid JSON: $out" >&2; return 1; }
  assert_eq "override" "true" "$(echo "$out" | jq -r .override)" || return 1
}

# Helper: build a fresh git repo with all six TARGETS committed to HEAD,
# then source preflight.sh in --override mode so check_no_dirty_target_edits
# and the TARGETS/failures globals are exercisable as a unit. The script's
# BASH_SOURCE guard prevents main() from running on source; we relax only
# errexit afterwards (matching test_poll_pr_reviews.sh) so the test driver
# can assert exit codes without aborting on the first failed assertion —
# nounset and pipefail stay on so the tests still catch the same shell
# bugs the rest of the suite catches.
with_sourced_sandbox() {
  local fn="$1"
  local sandbox; sandbox=$(mktemp -d "/tmp/test_preflight.${fn}.XXXXXX") || return 1
  (
    set -e
    cd "$sandbox"
    git -c init.defaultBranch=main init -q
    git -c user.email=t@t -c user.name=t commit --allow-empty -q -m init
    mkdir -p .github/workflows .github/aw
    touch .github/workflows/review-openai.md \
          .github/workflows/review-openai.lock.yml \
          .github/workflows/review-anthropic.md \
          .github/workflows/review-anthropic.lock.yml \
          .github/aw/actions-lock.json \
          .gitattributes \
          .env.example
    git add -A
    git -c user.email=t@t -c user.name=t commit -q -m targets
  ) || { local s=$?; rm -rf "$sandbox"; return $s; }

  (
    cd "$sandbox"
    # shellcheck disable=SC1090
    source "$SCRIPT" --override 2>/dev/null || true
    set +e
    "$fn"
  )
  local rc=$?
  rm -rf "$sandbox"
  return $rc
}

# Issue #79: a TARGET file deleted from the working tree (`rm <file>`)
# but still tracked at HEAD must surface as a dirty-target failure so
# scaffold.sh can't silently re-create it and clobber the consumer's
# intentional removal.
t_tracked_deletion_via_rm_flagged() {
  rm .gitattributes
  failures=()
  check_no_dirty_target_edits
  [[ ${#failures[@]} -eq 1 ]] || { echo "    FAIL: expected 1 failure, got ${#failures[@]}: ${failures[*]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "tracked deletion" || { echo "    FAIL: expected 'tracked deletion' marker; got: ${failures[0]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "\.gitattributes" || { echo "    FAIL: expected .gitattributes path; got: ${failures[0]}" >&2; return 1; }
}

# `git rm` form removes the path from index AND working tree while it
# stays in HEAD until the deletion is committed. The diff-filter=D
# check must catch this case too.
t_tracked_deletion_via_git_rm_flagged() {
  git rm -q .github/aw/actions-lock.json
  failures=()
  check_no_dirty_target_edits
  [[ ${#failures[@]} -eq 1 ]] || { echo "    FAIL: expected 1 failure, got ${#failures[@]}: ${failures[*]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "tracked deletion" || { echo "    FAIL: expected 'tracked deletion' marker; got: ${failures[0]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "actions-lock\.json" || { echo "    FAIL: expected actions-lock.json path; got: ${failures[0]}" >&2; return 1; }
}

# Multi-target deletion: every deleted target must surface, not just
# the first one found.
t_multiple_tracked_deletions_all_flagged() {
  rm .gitattributes
  git rm -q .github/workflows/review-openai.md
  failures=()
  check_no_dirty_target_edits
  [[ ${#failures[@]} -eq 1 ]] || { echo "    FAIL: expected 1 aggregated failure, got ${#failures[@]}: ${failures[*]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "\.gitattributes (tracked deletion)" || { echo "    FAIL: missing .gitattributes in: ${failures[0]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "review-openai\.md (tracked deletion)" || { echo "    FAIL: missing review-openai.md in: ${failures[0]}" >&2; return 1; }
}

# Sanity: with all targets present and unmodified, the check must not
# flag anything. Guards against a regression where the new branch
# misfires on the happy path.
t_unmodified_targets_not_flagged() {
  failures=()
  check_no_dirty_target_edits
  [[ ${#failures[@]} -eq 0 ]] || { echo "    FAIL: expected 0 failures, got ${#failures[@]}: ${failures[*]}" >&2; return 1; }
}

# .env.example is staged by commit.sh, so the override dirty-check must
# guard it too — otherwise a consumer's unrelated pending .env.example
# edits get swept into the reviewer-upgrade commit. Uncommitted edits on
# the tracked .env.example must surface as a dirty-target failure.
t_env_example_uncommitted_edits_flagged() {
  printf 'CONSUMER_VAR=pending\n' >> .env.example
  failures=()
  check_no_dirty_target_edits
  [[ ${#failures[@]} -eq 1 ]] || { echo "    FAIL: expected 1 failure, got ${#failures[@]}: ${failures[*]}" >&2; return 1; }
  echo "${failures[0]}" | grep -q "\.env\.example (uncommitted edits)" || { echo "    FAIL: expected '.env.example (uncommitted edits)'; got: ${failures[0]}" >&2; return 1; }
}

# --- driver ---

echo "== preflight.sh tests =="
run "missing jq emits structured failure (install mode)"   with_repo missing_jq_install   t_missing_jq_emits_structured_failure_install_mode
run "missing jq emits structured failure (override mode)"  with_repo missing_jq_override  t_missing_jq_emits_structured_failure_override_mode
run "tracked deletion via rm flagged (issue #79)"          with_sourced_sandbox t_tracked_deletion_via_rm_flagged
run "tracked deletion via git rm flagged (issue #79)"      with_sourced_sandbox t_tracked_deletion_via_git_rm_flagged
run "multiple tracked deletions all flagged"               with_sourced_sandbox t_multiple_tracked_deletions_all_flagged
run "unmodified targets not flagged (sanity)"              with_sourced_sandbox t_unmodified_targets_not_flagged
run "env.example uncommitted edits flagged (#103)"         with_sourced_sandbox t_env_example_uncommitted_edits_flagged

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
