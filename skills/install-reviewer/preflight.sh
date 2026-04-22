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
#           "failures": [{"check": "<name>", "reason": "<human text>"}, ...]}
#        When ok is false, each failure includes a concrete recovery
#        command where applicable.
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
TEMPLATE=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/review-workflow.md"

declare -a failures=()

push_failure() {
  failures+=("{\"check\":\"$1\",\"reason\":\"$2\"}")
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

check_template_present() {
  [[ -f "$TEMPLATE" ]] || \
    push_failure "template-present" "Template not found at ${TEMPLATE} — run 'tessl install jbaruch/coding-policy' first"
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

main() {
  check_in_git_worktree
  check_gh_installed
  # gh-cli-dependent checks only make sense if gh is present — otherwise they
  # emit follow-on failures that can't succeed until gh is installed first.
  if command -v gh >/dev/null 2>&1; then
    check_gh_authenticated
    check_gh_aw_installed
  fi
  check_template_present
  # Remaining checks depend on a git worktree with origin; skip if either is missing
  # so we don't leak confusing git-error diagnostics on top of the real failures.
  if git rev-parse --git-dir >/dev/null 2>&1; then
    check_origin_remote
    check_branch_not_local
    if git remote get-url origin >/dev/null 2>&1; then
      check_branch_not_remote
    fi
  fi

  local failures_json
  if [[ ${#failures[@]} -eq 0 ]]; then
    failures_json='[]'
  else
    failures_json="[$(IFS=,; echo "${failures[*]}")]"
  fi

  local ok="true"
  local rc=0
  if [[ ${#failures[@]} -gt 0 ]]; then
    ok="false"
    rc=1
  fi

  jq -n --argjson ok "$ok" --argjson failures "$failures_json" \
    '{ok: $ok, failures: $failures}'
  exit "$rc"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
