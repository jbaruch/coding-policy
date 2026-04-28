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

# --- driver ---

echo "== preflight.sh tests =="
run "missing jq emits structured failure (install mode)"   with_repo missing_jq_install   t_missing_jq_emits_structured_failure_install_mode
run "missing jq emits structured failure (override mode)"  with_repo missing_jq_override  t_missing_jq_emits_structured_failure_override_mode

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
