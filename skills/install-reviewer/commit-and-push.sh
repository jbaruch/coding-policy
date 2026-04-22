#!/usr/bin/env bash
# Stage the four files the install-reviewer skill produces, commit
# them with the canonical message, and push the feature branch to
# origin. Call this AFTER scaffold.sh has succeeded.
#
# Staged paths:
#   .github/workflows/review.md         — gh-aw source (new)
#   .github/workflows/review.lock.yml   — compiled workflow (new)
#   .github/aw/actions-lock.json        — shared gh-aw action pins
#                                         (new on first compile;
#                                         updated on subsequent ones)
#   .gitattributes                      — generated-file marker for
#                                         the lock (added by
#                                         scaffold.sh if missing)
#
# Usage: commit-and-push.sh
# Out:   one JSON object on stdout: {"commit": "<sha>", "remote_ref": "origin/<branch>"}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

set -euo pipefail

BRANCH="feat/add-coding-policy-review"
COMMIT_MSG="ci(review): add jbaruch/coding-policy PR review workflow"

FILES=(
  .github/workflows/review.md
  .github/workflows/review.lock.yml
  .github/aw/actions-lock.json
  .gitattributes
)

main() {
  # Stage only the files that actually exist; .gitattributes may be absent
  # if neither this skill nor any other tool has materialized it yet, but
  # scaffold.sh always creates it, so expect it here.
  local to_stage=()
  for f in "${FILES[@]}"; do
    [[ -e "$f" ]] && to_stage+=("$f")
  done
  if [[ ${#to_stage[@]} -eq 0 ]]; then
    echo "error: none of the expected install-reviewer files are present — run scaffold.sh first" >&2
    exit 1
  fi

  git add "${to_stage[@]}"

  # Fail clearly if the branch wasn't created (skill Step 3). Don't silently
  # commit on main or another branch.
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "$BRANCH" ]]; then
    echo "error: expected to be on '${BRANCH}' but current branch is '${current_branch}' — run 'git checkout -b ${BRANCH}' (skill Step 3) first" >&2
    exit 1
  fi

  if ! git commit -m "$COMMIT_MSG" >&2; then
    echo "error: 'git commit' failed — if a pre-commit hook rejected the change, fix the hook's finding and re-run (do NOT add --no-verify)" >&2
    exit 1
  fi

  local commit_sha
  commit_sha=$(git rev-parse HEAD)

  if ! git push -u origin "$BRANCH" >&2; then
    echo "error: 'git push' failed — the commit stands locally; inspect the remote and retry the push" >&2
    exit 1
  fi

  jq -n \
    --arg commit "$commit_sha" \
    --arg branch "$BRANCH" \
    '{commit: $commit, remote_ref: ("origin/" + $branch)}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
