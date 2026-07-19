#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh — the create/append/replace/idempotent
# behavior of the AGENTS.md `## Review guidelines` block and the wholesale
# write of .github/copilot-instructions.md, plus symlink refusal and the
# missing-template guard. Each test runs in its own throwaway git repo with
# the packaged templates copied to the plugin-mount path, so there is no
# shared mutable state (rules/testing-standards.md).
#
# Run: bash skills/install-reviewer/tests/test_scaffold.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${SKILL_DIR}/scaffold.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: scaffold.sh not readable at $SCRIPT" >&2; exit 2; }

BEGIN_MARKER="<!-- BEGIN jbaruch/coding-policy review guidelines -->"
TEMPLATE_MOUNT=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer"

FAIL_COUNT=0
PASS_COUNT=0

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Build a throwaway consumer repo with the two templates installed at the
# plugin-mount path, then run the test body inside it.
with_repo() {
  local fn="$1"
  local sandbox; sandbox=$(mktemp -d "/tmp/test_scaffold.${fn}.XXXXXX") || return 1
  (
    set -e
    cd "$sandbox"
    git -c init.defaultBranch=main init -q
    git -c user.email=t@t -c user.name=t commit --allow-empty -q -m init
    mkdir -p "$TEMPLATE_MOUNT"
    cp "${SKILL_DIR}/AGENTS_REVIEW_GUIDELINES.md" "${TEMPLATE_MOUNT}/"
    cp "${SKILL_DIR}/copilot-instructions.md" "${TEMPLATE_MOUNT}/"
  ) || { local s=$?; rm -rf "$sandbox"; return $s; }
  ( cd "$sandbox" && "$fn" )
  local rc=$?
  rm -rf "$sandbox"
  return $rc
}

count_markers() { grep -cF "$BEGIN_MARKER" AGENTS.md; }

# --- test bodies (run with cwd inside the sandbox) ---

t_create() {
  local out; out=$(bash "$SCRIPT") || { echo "    FAIL: scaffold exited non-zero" >&2; return 1; }
  [[ "$(jq -r .agents_action <<<"$out")" == "created" ]]  || { echo "    FAIL: agents_action != created: $out" >&2; return 1; }
  [[ "$(jq -r .copilot_action <<<"$out")" == "created" ]] || { echo "    FAIL: copilot_action != created: $out" >&2; return 1; }
  [[ "$(jq -r .state <<<"$out")" == "scaffolded" ]]       || { echo "    FAIL: state != scaffolded: $out" >&2; return 1; }
  [[ "$(count_markers)" == "1" ]]                         || { echo "    FAIL: expected exactly one block, got $(count_markers)" >&2; return 1; }
  [[ -f .github/copilot-instructions.md ]]                || { echo "    FAIL: copilot-instructions.md not written" >&2; return 1; }
}

t_idempotent() {
  bash "$SCRIPT" >/dev/null || return 1
  local out; out=$(bash "$SCRIPT") || { echo "    FAIL: second run exited non-zero" >&2; return 1; }
  [[ "$(jq -r .state <<<"$out")" == "no-op" ]]                  || { echo "    FAIL: re-run not no-op: $out" >&2; return 1; }
  [[ "$(jq -r .agents_action <<<"$out")" == "unchanged" ]]     || { echo "    FAIL: agents_action != unchanged: $out" >&2; return 1; }
  [[ "$(jq -r .copilot_action <<<"$out")" == "unchanged" ]]    || { echo "    FAIL: copilot_action != unchanged: $out" >&2; return 1; }
}

t_append_preserves_content() {
  printf '# My Agents\n\nConsumer content here.\n' > AGENTS.md
  local out; out=$(bash "$SCRIPT") || return 1
  [[ "$(jq -r .agents_action <<<"$out")" == "appended" ]] || { echo "    FAIL: agents_action != appended: $out" >&2; return 1; }
  grep -qF "Consumer content here." AGENTS.md || { echo "    FAIL: prior content not preserved" >&2; return 1; }
  [[ "$(count_markers)" == "1" ]]             || { echo "    FAIL: expected one block, got $(count_markers)" >&2; return 1; }
}

t_replace_removes_stale_keeps_content() {
  bash "$SCRIPT" >/dev/null || return 1
  printf '# Prefix content\n\n' | cat - AGENTS.md > AGENTS.md.new && mv AGENTS.md.new AGENTS.md
  # Corrupt the block interior with a STALE line.
  perl -0pi -e 's/(BEGIN jbaruch.*?-->\n)/$1STALE-LINE\n/s' AGENTS.md
  local out; out=$(bash "$SCRIPT") || return 1
  [[ "$(jq -r .agents_action <<<"$out")" == "replaced" ]] || { echo "    FAIL: agents_action != replaced: $out" >&2; return 1; }
  grep -qF "Prefix content" AGENTS.md || { echo "    FAIL: prefix content lost" >&2; return 1; }
  ! grep -qF "STALE-LINE" AGENTS.md   || { echo "    FAIL: STALE-LINE survived replace" >&2; return 1; }
  [[ "$(count_markers)" == "1" ]]     || { echo "    FAIL: expected one block after replace, got $(count_markers)" >&2; return 1; }
}

t_copilot_overwritten() {
  mkdir -p .github
  printf 'consumer copilot notes\n' > .github/copilot-instructions.md
  local out; out=$(bash "$SCRIPT") || return 1
  [[ "$(jq -r .copilot_action <<<"$out")" == "overwritten" ]] || { echo "    FAIL: copilot_action != overwritten: $out" >&2; return 1; }
  grep -qF "Copilot code review" .github/copilot-instructions.md || { echo "    FAIL: copilot template not written over" >&2; return 1; }
}

t_symlink_refused() {
  ln -s /etc/hostname AGENTS.md
  local rc=0; bash "$SCRIPT" >/dev/null 2>&1 || rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit on symlink target" >&2; return 1; }
  [[ -L AGENTS.md ]] || { echo "    FAIL: symlink was replaced instead of refused" >&2; return 1; }
}

t_missing_template_fails() {
  rm -f "${TEMPLATE_MOUNT}/AGENTS_REVIEW_GUIDELINES.md"
  local rc=0; bash "$SCRIPT" >/dev/null 2>&1 || rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit when a template is missing" >&2; return 1; }
}

echo "== scaffold.sh tests =="
run "create writes both artifacts"              with_repo t_create
run "re-run is a no-op"                         with_repo t_idempotent
run "append preserves consumer content"         with_repo t_append_preserves_content
run "replace removes stale, keeps content"      with_repo t_replace_removes_stale_keeps_content
run "pre-existing copilot file overwritten"     with_repo t_copilot_overwritten
run "symlink target refused"                    with_repo t_symlink_refused
run "missing template fails loudly"             with_repo t_missing_template_fails

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
