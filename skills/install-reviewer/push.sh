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
  # ls-remote failing (network/auth) is deliberately tolerated — fall through
  # and let `git push` report the real error. But branch on its exit code
  # instead of `... | awk | echo ""`, which collapsed two states into empty:
  # the branch legitimately absent (rc 0, no output) and ls-remote failing.
  # Both then read as "not on remote"; a real failure now warns rather than
  # passing silently (rules/error-handling.md Shell Error Handling).
  local local_sha remote_sha ls_out ls_rc=0
  local_sha=$(git rev-parse HEAD)
  ls_out=$(git ls-remote --heads origin "$BRANCH" 2>/dev/null) || ls_rc=$?
  if [[ $ls_rc -ne 0 ]]; then
    echo "push.sh: warning: 'git ls-remote origin ${BRANCH}' failed (rc=${ls_rc}) — remote state unknown; proceeding with the push, which will report the real error if any" >&2
    remote_sha=""
  else
    remote_sha=$(awk 'NR==1{print $1}' <<<"$ls_out")
  fi
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
