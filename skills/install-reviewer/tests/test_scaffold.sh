#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh — copies the fleet-reviewer opt-in files
# (the .github/fleet-review-enabled marker + .github/copilot-instructions.md)
# into a consumer repo. Each test runs in a throwaway git repo with the packaged
# templates copied to the plugin-mount path (no network, no shared state per
# rules/testing-standards.md).
#
# Run: bash skills/install-reviewer/tests/test_scaffold.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${SKILL_DIR}/scaffold.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: scaffold.sh not readable at $SCRIPT" >&2; exit 2; }

TEMPLATE_MOUNT=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/templates"
TARGETS=(.github/fleet-review-enabled .github/workflows/review-trigger.yml .github/copilot-instructions.md)

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }
run() { local n="$1"; shift; if "$@"; then ok "$n"; else bad "$n"; fi; }

with_repo() {
  local fn="$1"
  local sandbox; sandbox=$(mktemp -d "/tmp/test_scaffold.${fn}.XXXXXX") || return 1
  (
    set -e
    cd "$sandbox"
    git -c init.defaultBranch=main init -q
    git -c user.email=t@t -c user.name=t commit --allow-empty -q -m init
    mkdir -p "$TEMPLATE_MOUNT"
    cp "${SKILL_DIR}/templates/fleet-review-enabled.md" "$TEMPLATE_MOUNT/"
    cp "${SKILL_DIR}/templates/review-trigger.yml.md"    "$TEMPLATE_MOUNT/"
    cp "${SKILL_DIR}/templates/copilot-instructions.md" "$TEMPLATE_MOUNT/"
  ) || { local s=$?; rm -rf "$sandbox"; return $s; }
  ( cd "$sandbox" && "$fn" )
  local rc=$?
  rm -rf "$sandbox"
  return $rc
}

all_targets_present() { local t; for t in "${TARGETS[@]}"; do [[ -f "$t" ]] || return 1; done; return 0; }

t_install_creates_all() {
  local out; out=$(bash "$SCRIPT") || { echo "    FAIL: scaffold exited non-zero" >&2; return 1; }
  [[ "$(jq -r .state <<<"$out")" == "scaffolded" ]] || { echo "    FAIL: state != scaffolded: $out" >&2; return 1; }
  [[ "$(jq '[.files[] | select(.action=="created")] | length' <<<"$out")" == "3" ]] || { echo "    FAIL: expected 3 created: $out" >&2; return 1; }
  all_targets_present || { echo "    FAIL: not all 3 targets written" >&2; return 1; }
}

t_install_refuses_existing() {
  bash "$SCRIPT" >/dev/null || return 1
  local rc=0; bash "$SCRIPT" >/dev/null 2>&1 || rc=$?
  [[ $rc -eq 1 ]] || { echo "    FAIL: install did not refuse a pre-existing target (rc=$rc)" >&2; return 1; }
}

t_upgrade_overwrites() {
  bash "$SCRIPT" >/dev/null || return 1
  printf 'tampered\n' > .github/copilot-instructions.md
  local out; out=$(bash "$SCRIPT" --override) || return 1
  [[ "$(jq -r .override <<<"$out")" == "true" ]] || { echo "    FAIL: override flag not true" >&2; return 1; }
  grep -q "complementary lane" .github/copilot-instructions.md || { echo "    FAIL: copilot-instructions not restored from template" >&2; return 1; }
}

t_upgrade_noop_when_identical() {
  bash "$SCRIPT" >/dev/null || return 1
  local out; out=$(bash "$SCRIPT" --override) || return 1
  [[ "$(jq -r .state <<<"$out")" == "no-op" ]] || { echo "    FAIL: identical upgrade not no-op: $out" >&2; return 1; }
}

t_symlink_target_refused() {
  mkdir -p .github
  ln -s /etc/hostname .github/copilot-instructions.md
  local rc=0; bash "$SCRIPT" --override >/dev/null 2>&1 || rc=$?
  [[ $rc -ne 0 && -L .github/copilot-instructions.md ]] || { echo "    FAIL: symlink target not refused (rc=$rc)" >&2; return 1; }
}

t_nonregular_target_refused() {
  mkdir -p .github/copilot-instructions.md
  local rc=0; bash "$SCRIPT" --override >/dev/null 2>&1 || rc=$?
  [[ $rc -ne 0 && -d .github/copilot-instructions.md ]] || { echo "    FAIL: directory target not refused (rc=$rc)" >&2; return 1; }
}

t_missing_template_fails() {
  rm -f "$TEMPLATE_MOUNT/fleet-review-enabled.md"
  local rc=0; bash "$SCRIPT" >/dev/null 2>&1 || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: missing template did not fail" >&2; return 1; }
}

echo "== scaffold.sh tests =="
run "install creates all three artifacts"          with_repo t_install_creates_all
run "install refuses a pre-existing target"   with_repo t_install_refuses_existing
run "upgrade overwrites a tampered file"      with_repo t_upgrade_overwrites
run "upgrade is a no-op when identical"       with_repo t_upgrade_noop_when_identical
run "symlink target is refused"               with_repo t_symlink_target_refused
run "non-regular-file target is refused"      with_repo t_nonregular_target_refused
run "missing template fails loudly"           with_repo t_missing_template_fails
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
