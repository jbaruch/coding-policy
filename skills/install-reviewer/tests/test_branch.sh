#!/usr/bin/env bash
# Outcome-based tests for branch.sh covering every state branch.sh
# emits, plus its three refusal paths. Each test sets up an isolated
# temp git repo (with a bare-repo "origin" where remote-state matters)
# so test order doesn't matter and there's no shared mutable state per
# rules/testing-standards.md.
#
# Run: bash skills/install-reviewer/tests/test_branch.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/branch.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: branch.sh not executable at $SCRIPT" >&2; exit 2; }

INSTALL_BRANCH="feat/add-coding-policy-review"
UPGRADE_BRANCH="feat/upgrade-coding-policy-review"

FAIL_COUNT=0
PASS_COUNT=0

# Per-test sandbox: `with_repo <name> <body>` creates a fresh worktree
# with an "origin" bare repo, runs the body inside it, and cleans up
# regardless of body exit status.
with_repo() {
  local name="$1"; shift
  local root
  root=$(mktemp -d "/tmp/test_branch.${name}.XXXXXX") || return 1
  local repo="$root/repo"
  local origin="$root/origin.git"
  (
    set -e
    git -c init.defaultBranch=main init -q "$repo"
    cd "$repo"
    git commit --allow-empty -m init -q
    git init --bare -q "$origin"
    git remote add origin "$origin"
    git push -q origin main
    git remote set-head origin main >/dev/null
  ) || { rm -rf "$root"; return 1; }
  (
    cd "$repo"
    "$@"
  )
  local rc=$?
  rm -rf "$root"
  return $rc
}

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  local rc=0
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# --- test bodies ---

t_install_fresh_creates_branch() {
  local out
  out=$("$SCRIPT" 2>/dev/null) || return 1
  assert_eq "state" "created" "$(jq -r .state <<<"$out")" || return 1
  assert_eq "branch" "$INSTALL_BRANCH" "$(jq -r .branch <<<"$out")" || return 1
  assert_eq "override" "false" "$(jq -r .override <<<"$out")" || return 1
  assert_eq "current branch" "$INSTALL_BRANCH" "$(git rev-parse --abbrev-ref HEAD)" || return 1
}

t_install_idempotent_when_already_on_branch() {
  "$SCRIPT" >/dev/null || return 1
  local out
  out=$("$SCRIPT" 2>/dev/null) || return 1
  assert_eq "state" "already-on-branch" "$(jq -r .state <<<"$out")"
}

t_install_refuses_when_local_branch_already_exists() {
  git checkout -b "$INSTALL_BRANCH" -q
  git checkout main -q
  local err exit_code
  err=$("$SCRIPT" 2>&1 >/dev/null)
  exit_code=$?
  assert_eq "exit code" "1" "$exit_code" || return 1
  [[ "$err" == *"already exists"* ]] || { echo "    FAIL: expected 'already exists' in stderr, got: $err" >&2; return 1; }
}

t_upgrade_fresh_creates_from_default() {
  local out
  out=$("$SCRIPT" --override 2>/dev/null) || return 1
  assert_eq "state" "created" "$(jq -r .state <<<"$out")" || return 1
  assert_eq "branch" "$UPGRADE_BRANCH" "$(jq -r .branch <<<"$out")" || return 1
  assert_eq "override" "true" "$(jq -r .override <<<"$out")" || return 1
}

t_upgrade_idempotent_when_already_on_branch() {
  "$SCRIPT" --override >/dev/null || return 1
  local out
  out=$("$SCRIPT" --override 2>/dev/null) || return 1
  assert_eq "state" "already-on-branch" "$(jq -r .state <<<"$out")"
}

t_upgrade_remote_exists_local_does_not_tracks_remote() {
  git checkout -b "$UPGRADE_BRANCH" -q
  git commit --allow-empty -m "prior upgrade work" -q
  git push -q origin "$UPGRADE_BRANCH"
  git checkout main -q
  git branch -D "$UPGRADE_BRANCH" -q
  local out
  out=$("$SCRIPT" --override 2>/dev/null) || return 1
  assert_eq "state" "checked-out-tracking" "$(jq -r .state <<<"$out")" || return 1
  local upstream
  upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
  assert_eq "upstream" "origin/${UPGRADE_BRANCH}" "$upstream"
}

t_upgrade_both_exist_checks_out_local() {
  git checkout -b "$UPGRADE_BRANCH" -q
  git commit --allow-empty -m "prior upgrade work" -q
  git push -q origin "$UPGRADE_BRANCH"
  git checkout main -q
  local out
  out=$("$SCRIPT" --override 2>/dev/null) || return 1
  assert_eq "state" "checked-out" "$(jq -r .state <<<"$out")"
}

t_unknown_arg_refused_with_exit_2() {
  set +e
  "$SCRIPT" --bogus 2>/dev/null
  local rc=$?
  set -e
  assert_eq "exit code" "2" "$rc"
}

t_outside_git_worktree_refused() {
  # Step out of the test repo into a tmp dir with no .git
  local outside
  outside=$(mktemp -d "/tmp/outside.XXXXXX")
  set +e
  ( cd "$outside" && "$SCRIPT" 2>/dev/null )
  local rc=$?
  set -e
  rm -rf "$outside"
  assert_eq "exit code" "1" "$rc"
}

t_resolve_default_branch_falls_back_to_master() {
  # Repo whose default is `master` and origin/HEAD symref isn't set
  local out
  git checkout -b master -q
  git branch -d main -q 2>/dev/null || true
  git push -q origin master
  git push -q origin --delete main 2>/dev/null || true
  git symbolic-ref --delete refs/remotes/origin/HEAD 2>/dev/null || true
  out=$("$SCRIPT" 2>/dev/null) || return 1
  assert_eq "state" "created" "$(jq -r .state <<<"$out")"
}

# --- driver ---

echo "== branch.sh tests =="
run "install: fresh repo creates the install branch from default"  with_repo install_fresh                 t_install_fresh_creates_branch
run "install: re-run is idempotent when already on the branch"     with_repo install_idem                  t_install_idempotent_when_already_on_branch
run "install: refuses when local branch already exists"            with_repo install_refuse                t_install_refuses_when_local_branch_already_exists
run "upgrade: fresh repo creates the upgrade branch from default"  with_repo upgrade_fresh                 t_upgrade_fresh_creates_from_default
run "upgrade: re-run is idempotent when already on the branch"     with_repo upgrade_idem                  t_upgrade_idempotent_when_already_on_branch
run "upgrade: remote-only branch is checked out with tracking"     with_repo upgrade_remote                t_upgrade_remote_exists_local_does_not_tracks_remote
run "upgrade: both local and remote exist — local is checked out"  with_repo upgrade_both                  t_upgrade_both_exist_checks_out_local
run "refuses unknown argument with exit 2"                         with_repo unknown_arg                   t_unknown_arg_refused_with_exit_2
run "refuses when run outside a git worktree"                      with_repo outside_worktree              t_outside_git_worktree_refused
run "resolves default branch to master when origin/HEAD missing"   with_repo default_master                t_resolve_default_branch_falls_back_to_master

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
