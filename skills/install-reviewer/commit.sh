#!/usr/bin/env bash
# Stage the two files the install-reviewer skill produces and commit
# them with the canonical message. Call after scaffold.sh has succeeded
# and before push.sh.
#
# Idempotent per rules/file-hygiene.md: if nothing is staged because
# the working tree already matches a prior successful run, the script
# emits {"state": "no-op", ...} with exit 0 instead of failing the way
# `git commit` would.
#
# Staged paths:
#   AGENTS.md
#   .github/copilot-instructions.md
#
# Usage: commit.sh [--override]
#   --override    Upgrade-mode commit. Uses the upgrade branch name and
#                 a different commit message ("upgrade" vs "add"); same
#                 staged paths.
# Out:   one JSON object on stdout: {"state": "committed|no-op", "commit": "<sha>", "override": bool}
# Exit:  0 on success (including no-op); non-zero with stderr diagnostic on failure

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done

# Run from repo root so the relative paths below resolve regardless of cwd.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "error: not inside a git worktree — run from within the consumer repo" >&2
  exit 1
}
cd "$repo_root"

if (( OVERRIDE_MODE == 1 )); then
  BRANCH="feat/upgrade-coding-policy-review"
  COMMIT_MSG="ci(review): upgrade jbaruch/coding-policy PR review setup"
else
  BRANCH="feat/add-coding-policy-review"
  COMMIT_MSG="ci(review): add jbaruch/coding-policy PR review setup"
fi

FILES=(
  AGENTS.md
  .github/copilot-instructions.md
)

main() {
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "$BRANCH" ]]; then
    echo "error: expected to be on '${BRANCH}' but current branch is '${current_branch}' — run 'git checkout -b ${BRANCH}' first" >&2
    exit 1
  fi

  # Both artifacts must land together — refuse to commit a partial scaffold
  # (e.g., a file deleted between scaffold and commit). If any expected
  # artifact is missing, list every missing path and fail; do not stage
  # what's present.
  local missing=()
  for f in "${FILES[@]}"; do
    [[ -e "$f" ]] || missing+=("$f")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "error: partial scaffold — expected files missing: ${missing[*]} — run scaffold.sh first (or restore the missing files) so both artifacts land together" >&2
    exit 1
  fi

  git add "${FILES[@]}"

  # Idempotent re-run: nothing staged means a prior run already committed
  # this state. Emit no-op success instead of letting `git commit` fail.
  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  if git diff --cached --quiet; then
    jq -n --arg commit "$(git rev-parse HEAD)" --argjson override "$override_json" \
      '{state: "no-op", commit: $commit, override: $override}'
    return 0
  fi

  if ! git commit -m "$COMMIT_MSG" >&2; then
    echo "error: 'git commit' failed — if a pre-commit hook rejected the change, fix the hook's finding and re-run (do NOT add --no-verify)" >&2
    exit 1
  fi

  jq -n --arg commit "$(git rev-parse HEAD)" --argjson override "$override_json" \
    '{state: "committed", commit: $commit, override: $override}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
