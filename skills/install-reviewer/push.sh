#!/usr/bin/env bash
# Push the install-reviewer feature branch to origin. Call AFTER
# commit.sh has produced the commit.
#
# Idempotent per rules/file-hygiene.md: if origin/<branch> already
# matches the local HEAD, the script emits {"state": "up-to-date", ...}
# with exit 0 instead of letting a redundant push produce noise.
#
# Usage: push.sh [--override]
#   --override    Push the upgrade branch instead of the install branch.
# Out:   one JSON object on stdout: {"state": "pushed|up-to-date", "remote_ref": "origin/<branch>", "override": bool}
# Exit:  0 on success (including up-to-date); non-zero with stderr diagnostic on failure

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done

# Run from repo root so git commands resolve predictably.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "error: not inside a git worktree — run from within the consumer repo" >&2
  exit 1
}
cd "$repo_root"

if (( OVERRIDE_MODE == 1 )); then
  BRANCH="feat/upgrade-coding-policy-review"
else
  BRANCH="feat/add-coding-policy-review"
fi

main() {
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "$BRANCH" ]]; then
    echo "error: expected to be on '${BRANCH}' but current branch is '${current_branch}'" >&2
    exit 1
  fi

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  # If the remote branch already matches local HEAD, skip the push.
  # ls-remote can fail on network/auth issues — treat that as "unknown remote
  # state" and proceed with the push so git push can report the real error.
  # set -o pipefail means a failure in ls-remote would kill us here without
  # the explicit `|| true` guard.
  local local_sha remote_sha
  local_sha=$(git rev-parse HEAD)
  remote_sha=$(git ls-remote --heads origin "$BRANCH" 2>/dev/null | awk '{print $1}' || echo "")
  if [[ -n "$remote_sha" && "$remote_sha" == "$local_sha" ]]; then
    jq -n --arg branch "$BRANCH" --argjson override "$override_json" \
      '{state: "up-to-date", remote_ref: ("origin/" + $branch), override: $override}'
    return 0
  fi

  if ! git push -u origin "$BRANCH" >&2; then
    echo "error: 'git push' failed — the commit stands locally; inspect the remote and retry" >&2
    exit 1
  fi

  jq -n --arg branch "$BRANCH" --argjson override "$override_json" \
    '{state: "pushed", remote_ref: ("origin/" + $branch), override: $override}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
