#!/usr/bin/env bash
# Establish the onboard-repo feature branch. Call AFTER preflight.sh
# (and AFTER the install-mode overwrite refusal in Step 2 of the skill)
# but BEFORE scaffold.sh — the scaffold's writes need to land on the
# feature branch, not on the consumer's default branch.
#
# Idempotent per rules/file-hygiene.md: if already on the target branch
# the script emits {"state": "already-on-branch", ...} with exit 0.
#
# Usage: branch.sh [--override]
#   --override    Establish the upgrade branch instead of the install branch.
# Out:   one JSON object on stdout:
#          {"state": "created|checked-out|checked-out-tracking|already-on-branch",
#           "branch": "<branch-name>", "override": bool}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

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

emit() {
  local state="$1"
  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"
  jq -n --arg state "$state" --arg branch "$BRANCH" --argjson override "$override_json" \
    '{state: $state, branch: $branch, override: $override}'
}

# Does <ref> exist? 0 present, 1 absent — both expected. A `git show-ref`
# exit >1 is a real fault (unreadable/corrupt repo), which this returns as
# 2 so callers surface it instead of reading it as "absent"
# (rules/error-handling.md — distinguish an expected non-result from a tool
# failure). No `--quiet` needed: stdout is redirected by the caller.
ref_exists() {
  local rc=0
  git show-ref --verify --quiet "$1" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    *) echo "error: 'git show-ref $1' failed (rc=${rc}) — the repository may be unreadable or corrupt; check 'git status' and repository ownership, then re-run" >&2; exit 2 ;;
  esac
}

# Determine the repo's default branch from origin/HEAD. The symref is set
# by `git clone` and `git remote set-head`; if it's missing we fall back to
# probing `origin/main` then `origin/master` (the two common conventions).
# A repo with neither is an unusual configuration the consumer fixes by hand.
resolve_default_branch() {
  local ref rc=0
  # `--quiet` is what makes the codes distinguishable: WITHOUT it, git
  # returns 128 for BOTH "HEAD symref not set" (expected) AND a real fault,
  # so they can't be told apart. WITH it, an unset/non-symref HEAD is rc 1
  # (expected, probe candidates) and only a genuine fault stays 128 —
  # surfaced rather than silently falling through to the main/master guess
  # (rules/error-handling.md — expected non-result vs tool failure).
  ref=$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null) || rc=$?
  case "$rc" in
    0) echo "${ref#refs/remotes/origin/}"; return 0 ;;
    1) ;;  # HEAD symref not set: fall through to candidate probing
    *) echo "error: 'git symbolic-ref refs/remotes/origin/HEAD' failed (rc=${rc}) — the repository may be unreadable; check 'git status', then re-run" >&2; exit 2 ;;
  esac
  for candidate in main master; do
    if ref_exists "refs/remotes/origin/${candidate}"; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

main() {
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)

  # Idempotent re-entry: if we're already on the target branch, no-op.
  if [[ "$current_branch" == "$BRANCH" ]]; then
    emit "already-on-branch"
    return 0
  fi

  if (( OVERRIDE_MODE == 0 )); then
    # Install mode: create from default branch. If the local branch
    # already exists (consumer ran the skill before, branch lingering)
    # we'd produce a divergent state — refuse with a clear diagnostic.
    if ref_exists "refs/heads/${BRANCH}"; then
      echo "error: local branch '${BRANCH}' already exists — install mode expects a clean slate; remove the branch or run with --override to use the upgrade flow" >&2
      exit 1
    fi
    local default_branch
    if ! default_branch=$(resolve_default_branch); then
      echo "error: cannot resolve origin's default branch — set it with 'git remote set-head origin --auto', or check out main/master before re-running" >&2
      exit 1
    fi
    git checkout "$default_branch" >&2
    git checkout -b "$BRANCH" >&2
    emit "created"
    return 0
  fi

  # Upgrade mode: pick the path based on what's already there. Running
  # from a fresh clone while a prior upgrade PR is still open is a real
  # case — naively `git checkout -b` from default would produce a
  # divergent local branch that can't fast-forward push.
  local remote_exists=0
  set +e
  git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1
  local ls_exit=$?
  set -e
  case $ls_exit in
    0) remote_exists=1 ;;
    2) remote_exists=0 ;;
    *)
      echo "error: 'git ls-remote' failed with exit ${ls_exit} — likely a network or auth issue; resolve and retry" >&2
      exit 1
      ;;
  esac

  if (( remote_exists == 1 )); then
    # Surface the remote ref locally so subsequent --track works.
    git fetch origin "$BRANCH" >&2
  fi

  local local_exists=0
  if ref_exists "refs/heads/${BRANCH}"; then
    local_exists=1
  fi

  if (( local_exists == 1 )); then
    git checkout "$BRANCH" >&2
    emit "checked-out"
  elif (( remote_exists == 1 )); then
    git checkout -b "$BRANCH" --track "origin/${BRANCH}" >&2
    emit "checked-out-tracking"
  else
    local default_branch
    if ! default_branch=$(resolve_default_branch); then
      echo "error: cannot resolve origin's default branch — set it with 'git remote set-head origin --auto', or check out main/master before re-running" >&2
      exit 1
    fi
    git checkout "$default_branch" >&2
    git checkout -b "$BRANCH" >&2
    emit "created"
  fi
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
