#!/usr/bin/env bash
# Run all install-reviewer preconditions and report them as one JSON
# result. The skill invokes this before any mutation so every preflight
# failure is surfaced together, not one-at-a-time. Checks cover: git
# worktree, GitHub CLI installation + auth, gh-aw extension, tile
# template presence, origin remote, and local + remote branch clear.
#
# Usage: preflight.sh
# Out:   one JSON object on stdout:
#          {"ok": bool,
#           "failures": [{"check": "<name>", "reason": "<human text>"}, ...],
#           "warnings": [{"check": "<name>", "reason": "<human text>"}, ...]}
#        When ok is false, each failure includes a concrete recovery
#        command where applicable. Warnings are informational only —
#        they surface advisory findings (e.g. repo-level .mcp.json that
#        would break the Anthropic reviewer at runtime) and never set
#        ok to false or change the exit code.
# Exit:  0 if ok is true; 1 if any check fails

set -euo pipefail

# If we're inside a git worktree, run from its root so the TEMPLATE path
# below resolves the same way regardless of the caller's cwd. If we're
# NOT in a worktree, the check_in_git_worktree step below will fail
# cleanly; don't exit here — we want to surface all preflight failures
# as structured JSON, not die early.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -n "$repo_root" ]]; then
  cd "$repo_root"
fi

BRANCH="feat/add-coding-policy-review"
TEMPLATE_DIR=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer"
TEMPLATES=(
  "${TEMPLATE_DIR}/review-openai.md"
  "${TEMPLATE_DIR}/review-anthropic.md"
)

declare -a failures=()
declare -a warnings=()

push_failure() {
  failures+=("{\"check\":\"$1\",\"reason\":\"$2\"}")
}

push_warning() {
  warnings+=("{\"check\":\"$1\",\"reason\":\"$2\"}")
}

check_in_git_worktree() {
  git rev-parse --git-dir >/dev/null 2>&1 || \
    push_failure "in-git-worktree" "Not inside a git worktree — run the skill from the root of the consumer repo's git checkout"
}

check_origin_remote() {
  git remote get-url origin >/dev/null 2>&1 || \
    push_failure "origin-remote" "No git remote named 'origin' — add one with 'git remote add origin <url>' before re-running (the push step assumes origin exists)"
}

check_gh_installed() {
  command -v gh >/dev/null 2>&1 || \
    push_failure "gh-installed" "GitHub CLI not found on PATH — install from https://cli.github.com/"
}

check_gh_authenticated() {
  gh auth status >/dev/null 2>&1 || \
    push_failure "gh-authenticated" "GitHub CLI not authenticated — run 'gh auth login'"
}

check_gh_aw_installed() {
  gh aw --version >/dev/null 2>&1 || \
    push_failure "gh-aw-installed" "gh-aw extension missing — run 'gh extension install github/gh-aw'"
}

check_templates_present() {
  local missing=()
  for t in "${TEMPLATES[@]}"; do
    [[ -f "$t" ]] || missing+=("$t")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    push_failure "templates-present" "Template(s) not found: ${missing[*]} — run 'tessl install jbaruch/coding-policy' first"
  fi
}

check_branch_not_local() {
  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    push_failure "branch-not-local" "Local branch '${BRANCH}' already exists — delete with 'git branch -D ${BRANCH}' or rename before re-running"
  fi
}

check_branch_not_remote() {
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    push_failure "branch-not-remote" "Remote branch 'origin/${BRANCH}' already exists — delete with 'git push origin --delete ${BRANCH}' or rename before re-running"
  fi
}

# Advisory check (not a failure): a committed .mcp.json at the repo root
# will be auto-loaded by Claude Code in the Anthropic reviewer's sandbox.
# Any stdio MCP server whose binary isn't on the awf sandbox's PATH
# (tessl is the common case) fails to launch and gh-aw fails the job
# even when the review itself would have run cleanly. The install flow
# can't fix this on the consumer's behalf (gh-aw has no post-checkout
# hook, Claude Code CLI 2.1.98 has no skip-project-mcp flag), so we
# surface the finding as a warning and include the workaround in the PR.
check_root_mcp_json_absent() {
  # Only flag if the file is tracked by git — an untracked .mcp.json is
  # a local-dev artifact that won't land in the PR head and won't affect
  # the workflow runs.
  if git ls-files --error-unmatch .mcp.json >/dev/null 2>&1; then
    push_warning "root-mcp-json-present" "Repo contains a committed '.mcp.json' at the root. Claude Code auto-loads it in the Anthropic reviewer's sandbox, and any stdio MCP server it declares (e.g. 'tessl mcp start') will fail to launch because its binary is not on the awf sandbox's PATH — gh-aw will fail the job even though the review would have run. Before the Anthropic reviewer can run cleanly, add '.mcp.json' to .gitignore, rename the committed file to '.mcp.json.example' for local-dev handoff, and commit that change (either in this install-reviewer PR or a follow-up). The install-reviewer skill does NOT do this automatically — it is a consumer-side decision."
  fi
}

main() {
  check_in_git_worktree
  check_gh_installed
  # gh-cli-dependent checks only make sense if gh is present — otherwise they
  # emit follow-on failures that can't succeed until gh is installed first.
  if command -v gh >/dev/null 2>&1; then
    check_gh_authenticated
    check_gh_aw_installed
  fi
  check_templates_present
  # Remaining checks depend on a git worktree with origin; skip if either is missing
  # so we don't leak confusing git-error diagnostics on top of the real failures.
  if git rev-parse --git-dir >/dev/null 2>&1; then
    check_origin_remote
    check_branch_not_local
    if git remote get-url origin >/dev/null 2>&1; then
      check_branch_not_remote
    fi
    # Advisory checks only run in a git worktree (they use `git ls-files`).
    check_root_mcp_json_absent
  fi

  local failures_json
  if [[ ${#failures[@]} -eq 0 ]]; then
    failures_json='[]'
  else
    failures_json="[$(IFS=,; echo "${failures[*]}")]"
  fi

  local warnings_json
  if [[ ${#warnings[@]} -eq 0 ]]; then
    warnings_json='[]'
  else
    warnings_json="[$(IFS=,; echo "${warnings[*]}")]"
  fi

  local ok="true"
  local rc=0
  if [[ ${#failures[@]} -gt 0 ]]; then
    ok="false"
    rc=1
  fi

  jq -n --argjson ok "$ok" --argjson failures "$failures_json" --argjson warnings "$warnings_json" \
    '{ok: $ok, failures: $failures, warnings: $warnings}'
  exit "$rc"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
