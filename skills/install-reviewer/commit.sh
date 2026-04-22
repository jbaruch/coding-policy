#!/usr/bin/env bash
# Stage the four files the install-reviewer skill produces and commit
# them with the canonical message. Call after scaffold.sh has succeeded
# and before push.sh.
#
# Idempotent per rules/file-hygiene.md: if nothing is staged because
# the working tree already matches a prior successful run, the script
# emits {"state": "no-op", ...} with exit 0 instead of failing the way
# `git commit` would.
#
# Staged paths:
#   .github/workflows/review.md
#   .github/workflows/review.lock.yml
#   .github/aw/actions-lock.json
#   .gitattributes
#
# Usage: commit.sh
# Out:   one JSON object on stdout: {"state": "committed|no-op", "commit": "<sha>"}
# Exit:  0 on success (including no-op); non-zero with stderr diagnostic on failure

set -euo pipefail

# Run from repo root so the relative paths below resolve regardless of cwd.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "error: not inside a git worktree — run from within the consumer repo" >&2
  exit 1
}
cd "$repo_root"

BRANCH="feat/add-coding-policy-review"
COMMIT_MSG="ci(review): add jbaruch/coding-policy PR review workflow"

FILES=(
  .github/workflows/review.md
  .github/workflows/review.lock.yml
  .github/aw/actions-lock.json
  .gitattributes
)

main() {
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "$BRANCH" ]]; then
    echo "error: expected to be on '${BRANCH}' but current branch is '${current_branch}' — run 'git checkout -b ${BRANCH}' first" >&2
    exit 1
  fi

  local to_stage=()
  for f in "${FILES[@]}"; do
    [[ -e "$f" ]] && to_stage+=("$f")
  done
  if [[ ${#to_stage[@]} -eq 0 ]]; then
    echo "error: none of the expected install-reviewer files are present — run scaffold.sh first" >&2
    exit 1
  fi

  git add "${to_stage[@]}"

  # Idempotent re-run: nothing staged means a prior run already committed
  # this state. Emit no-op success instead of letting `git commit` fail.
  if git diff --cached --quiet; then
    jq -n --arg commit "$(git rev-parse HEAD)" \
      '{state: "no-op", commit: $commit}'
    return 0
  fi

  if ! git commit -m "$COMMIT_MSG" >&2; then
    echo "error: 'git commit' failed — if a pre-commit hook rejected the change, fix the hook's finding and re-run (do NOT add --no-verify)" >&2
    exit 1
  fi

  jq -n --arg commit "$(git rev-parse HEAD)" \
    '{state: "committed", commit: $commit}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
