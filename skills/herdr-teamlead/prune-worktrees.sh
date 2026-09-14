#!/usr/bin/env bash
# Remove the worker worktrees and local branches a round left behind.
#
# Every Herdr round provisions worktrees under the worktree root
# (`provision-worktree.sh`) and the lead removes the task's own worktree after
# its merge (`rules/agent-worktree-isolation.md` Cleanup). Review, test and
# verify worktrees from earlier rounds, and the local branches a removed
# worktree leaves behind, accumulate until someone notices `git worktree list`
# scrolling. Which of them are safe to remove is one right answer per input,
# so the decision lives here (`rules/script-delegation.md`).
#
# Decision predicate — a worktree is REMOVED (and its branch deleted) iff:
#   * it is not the shared checkout itself,
#   * it lies under the worktree root,
#   * it is on a branch (not detached) other than origin's default branch,
#   * it is not locked,
#   * `git status --porcelain` is empty — untracked files count as dirty;
#     ignored files do not, they are reproducible by the ignore's own claim
#     and `git worktree remove` treats them the same way,
#   * its branch is an ancestor of origin's default branch (fully merged),
#     judged against refs fetched by THIS run and origin's default branch as
#     re-queried by THIS run — a fetch or default-branch lookup that fails is
#     a precondition failure, never a judgment from stale refs.
# A local branch with no worktree is DELETED iff it is not the default branch
# and is an ancestor of origin's default branch. That ancestry check is the
# safety; deletion is then `git branch -D`, since `-d` re-checks against the
# shared checkout's own default branch, which may lag origin's and refuse a
# branch this script just proved merged. Removal is `git worktree remove`,
# never `rm -rf`. Everything else is KEPT and reported with its reason. Stale
# worktree metadata is pruned last (`git worktree prune --expire now`), after
# every decision, and skipped when any worktree could not be entered: git
# would read an unreadable directory as gone and drop its entry. Nothing here
# pushes to origin; a dry run still fetches (without --prune), reads origin's
# default branch with `ls-remote --symref` instead of rewriting origin/HEAD,
# and skips the metadata prune, so its decisions are current and .git is
# otherwise untouched.
#
# Contract:
#   argv  : <shared-checkout> [--dry-run]
#           --dry-run reports the same decisions and changes nothing.
#   stdout: one JSON object —
#           {"shared":"<abs>","default_branch":"<name>","dry_run":bool,
#            "worktrees_removed":[{"path","branch"}],
#            "worktrees_kept":[{"path","branch","reason"}],
#            "branches_deleted":["<name>"],
#            "branches_kept":[{"branch","reason"}],
#            "failed":[{"target","error"}]}
#           reason is one of: default-branch, detached, dirty, locked,
#           outside-root, prunable (its directory is gone; a live run's
#           metadata prune removes it), unmerged.
#   stderr: diagnostics only.
#   exit  : 0 every decision applied (or previewed),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             no origin, fetch failed, default branch unresolvable, worktree
#             or branch inventory unreadable) — no JSON, nothing decided,
#           2 at least one check, removal or deletion failed; the rest still
#             ran and `failed` names each one.
#   env   : WORKTREE_ROOT overrides the worktree root (default
#           $HOME/.worktrees); the tests point it at a temp dir.
set -euo pipefail

ERRFILE=""
ROWS=""

warn() { printf 'prune-worktrees: %s\n' "$1" >&2; }

cleanup() {
  local f
  for f in "$ERRFILE" "$ROWS"; do
    if [[ -n "$f" ]] && ! rm -f "$f"; then
      warn "could not remove temp file ${f} — remove it by hand"
    fi
  done
  return 0
}

# Append one decision row: <kind> <target> <branch> <reason>, NUL-delimited
# so a path or diagnostic holding a tab or newline cannot shift the fields.
row() {
  printf '%s\0%s\0%s\0%s\0' "$1" "$2" "$3" "$4" >> "$ROWS"
  # A failure is a diagnostic on stderr as well as a JSON row
  # (rules/script-delegation.md Script Requirements).
  if [[ "$1" == failed ]]; then
    warn "${2}: ${4} — inspect it by hand; nothing else was skipped on its account"
  fi
}

# Echo origin's default branch name, or return 1 when none can be confirmed.
# A live run has just rewritten origin/HEAD from the remote; a dry run reads
# the remote's HEAD with `ls-remote --symref` instead, rewriting nothing.
default_branch_of() { # <shared> <dry-run 0|1>
  local db="" rc=0
  if (( $2 )); then
    local sym
    sym="$(git -C "$1" ls-remote --symref origin HEAD 2>"$ERRFILE")" || rc=$?
    if (( rc != 0 )); then
      warn "\`git ls-remote --symref origin HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
      return 1
    fi
    db="$(printf '%s\n' "$sym" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
    if [[ -n "$db" ]] && git -C "$1" show-ref --verify --quiet "refs/remotes/origin/${db}"; then
      printf '%s' "$db"; return 0
    fi
  else
    db="$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>"$ERRFILE")" || rc=$?
    case "$rc" in
      0) db="${db#origin/}"
         # A dangling origin/HEAD names a branch with no remote-tracking ref.
         if git -C "$1" show-ref --verify --quiet "refs/remotes/origin/${db}"; then printf '%s' "$db"; return 0; fi ;;
      1) ;;  # origin/HEAD is simply absent: fall back to the conventional names
      *) warn "\`git symbolic-ref refs/remotes/origin/HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"; return 1 ;;
    esac
  fi
  local cand
  for cand in main master; do
    if git -C "$1" show-ref --verify --quiet "refs/remotes/origin/$cand"; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

# Echo merged|unmerged for <branch> against origin/<default>, or return 2 on
# a tool failure. `merge-base --is-ancestor` exits 1 for "not an ancestor" and
# anything else for an invalid ref or repository error; collapsing both into
# "unmerged" would hide the failure behind a kept row
# (rules/error-handling.md Shell Error Handling).
ancestry() { # <shared> <branch> <default>
  local rc=0
  git -C "$1" merge-base --is-ancestor "$2" "origin/$3" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0) printf 'merged' ;;
    1) printf 'unmerged' ;;
    *) return 2 ;;
  esac
  return 0
}

# Decide one worktree; emits a row and performs the removal unless dry-run.
decide_worktree() { # <shared> <abs_root> <default> <dry-run 0|1> <path> <branch|""> <detached 0|1> <locked 0|1>
  local shared="$1" abs_root="$2" db="$3" dry="$4" path="$5" branch="$6" detached="$7" locked="$8"
  if [[ "$path" != "$abs_root"/* ]]; then
    row kept "$path" "$branch" outside-root; return 0
  fi
  if (( detached )); then
    row kept "$path" "" detached; return 0
  fi
  if [[ "$branch" == "$db" ]]; then
    row kept "$path" "$branch" default-branch; return 0
  fi
  if (( locked )); then
    row kept "$path" "$branch" locked; return 0
  fi
  if [[ ! -d "$path" ]]; then
    row kept "$path" "$branch" prunable; return 0
  fi
  local status rc=0
  status="$(git -C "$path" status --porcelain 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    row failed "$path" "$branch" "git status failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ -n "$status" ]]; then
    row kept "$path" "$branch" dirty; return 0
  fi
  local merged
  if ! merged="$(ancestry "$shared" "$branch" "$db")"; then
    row failed "$path" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ "$merged" == unmerged ]]; then
    row kept "$path" "$branch" unmerged; return 0
  fi
  if (( dry )); then
    row removed "$path" "$branch" ""; return 0
  fi
  if ! git -C "$shared" worktree remove "$path" 2>"$ERRFILE"; then
    row failed "$path" "$branch" "git worktree remove failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if ! git -C "$shared" branch -D "$branch" >/dev/null 2>"$ERRFILE"; then
    row failed "$branch" "$branch" "git branch -D failed after the worktree was removed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  row removed "$path" "$branch" ""
  return 0
}

decide_branch() { # <shared> <default> <dry-run 0|1> <branch>
  local shared="$1" db="$2" dry="$3" branch="$4"
  if [[ "$branch" == "$db" ]]; then
    return 0
  fi
  local merged
  if ! merged="$(ancestry "$shared" "$branch" "$db")"; then
    row failed "$branch" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ "$merged" == unmerged ]]; then
    row branch-kept "$branch" "$branch" unmerged; return 0
  fi
  if (( dry )); then
    row branch-deleted "$branch" "$branch" ""; return 0
  fi
  if ! git -C "$shared" branch -D "$branch" >/dev/null 2>"$ERRFILE"; then
    row failed "$branch" "$branch" "git branch -D failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  row branch-deleted "$branch" "$branch" ""
  return 0
}

main() {
  local shared="" dry=0 arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=1 ;;
      -*) warn "unknown flag '${arg}'"; warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"; return 1 ;;
      *) if [[ -n "$shared" ]]; then warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"; return 1; fi; shared="$arg" ;;
    esac
  done
  if [[ -z "$shared" ]]; then
    warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"
    return 1
  fi
  local tool
  for tool in git python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      warn "${tool} not found on PATH"
      return 1
    fi
  done
  ERRFILE="$(mktemp)"
  ROWS="$(mktemp)"
  trap cleanup EXIT
  if [[ ! -d "$shared" ]] || ! git -C "$shared" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "'${shared}' is not a git work tree — pass the shared checkout's path"
    return 1
  fi
  local abs_shared
  abs_shared="$(git -C "$shared" rev-parse --show-toplevel)"
  if ! git -C "$shared" remote get-url origin >/dev/null 2>&1; then
    warn "${shared} has no origin remote — merged-ness is judged against origin's default branch"
    return 1
  fi
  local root="${WORKTREE_ROOT:-${HOME}/.worktrees}" abs_root
  if [[ ! -d "$root" ]]; then
    warn "worktree root ${root} does not exist — nothing to prune"
    abs_root="$root"
  else
    abs_root="$(cd "$root" && pwd -P)"
  fi
  # Merged-ness is only as good as the refs it is judged against: a stale
  # origin/<default> after a force-push, or a cached origin/HEAD after the
  # remote's default branch moved, would delete work origin no longer holds.
  local -a fetch_args=(fetch --quiet origin)
  if (( ! dry )); then fetch_args=(fetch --quiet --prune origin); fi
  if ! git -C "$shared" "${fetch_args[@]}" 2>"$ERRFILE"; then
    warn "\`git -C ${shared} ${fetch_args[*]}\` failed: $(tr '\n' ' ' < "$ERRFILE") — check connectivity; refusing to judge merged-ness from stale refs"
    return 1
  fi
  if (( ! dry )) && ! git -C "$shared" remote set-head origin --auto >/dev/null 2>"$ERRFILE"; then
    warn "\`git -C ${shared} remote set-head origin --auto\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot confirm origin's current default branch"
    return 1
  fi
  local db
  if ! db="$(default_branch_of "$shared" "$dry")"; then
    warn "cannot resolve origin's default branch — run \`git -C ${shared} remote set-head origin --auto\`"
    return 1
  fi

  # Take both inventories up front: a failure inside a process substitution
  # would not reach the loop, and an empty inventory would read as "nothing
  # to prune" with exit 0 (rules/file-hygiene.md I/O Conventions). Nothing has
  # been decided yet, so an unreadable inventory is a precondition failure.
  local inventory branches
  inventory="$(mktemp)"; branches="$(mktemp)"
  if ! git -C "$shared" worktree list --porcelain >"$inventory" 2>"$ERRFILE"; then
    warn "\`git worktree list --porcelain\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory worktrees"
    rm -f "$inventory" "$branches"
    return 1
  fi
  if ! git -C "$shared" for-each-ref --format='%(refname:short)' refs/heads/ >"$branches" 2>"$ERRFILE"; then
    warn "\`git for-each-ref refs/heads/\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory branches"
    rm -f "$inventory" "$branches"
    return 1
  fi

  # Walk `worktree list --porcelain`: blank-line-separated blocks.
  local path="" branch="" detached=0 locked=0 line unenterable=0
  local -a seen_branches=()
  flush() {
    if [[ -n "$path" ]]; then
      if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
      local real="" rc=0
      if [[ ! -e "$path" ]]; then
        # Its directory is gone: an expected non-result, decided as prunable.
        real="$path"
      else
        real="$(cd "$path" 2>"$ERRFILE" && pwd -P)" || rc=$?
        if (( rc != 0 )); then
          row failed "$path" "$branch" "cannot enter the worktree: $(tr '\n' ' ' < "$ERRFILE")"
          unenterable=1
          path=""; branch=""; detached=0; locked=0
          return 0
        fi
      fi
      if [[ "$real" != "$abs_shared" ]]; then
        decide_worktree "$shared" "$abs_root" "$db" "$dry" "$real" "$branch" "$detached" "$locked"
      fi
    fi
    path=""; branch=""; detached=0; locked=0
  }
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      "worktree "*) flush; path="${line#worktree }" ;;
      "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
      detached) detached=1 ;;
      "locked"*) locked=1 ;;
      "") flush ;;
    esac
  done < "$inventory"
  flush

  # Local branches with no worktree.
  local name skip
  while IFS= read -r name; do
    skip=0
    local s
    for s in "${seen_branches[@]+"${seen_branches[@]}"}"; do
      if [[ "$s" == "$name" ]]; then skip=1; break; fi
    done
    if (( skip )); then continue; fi
    decide_branch "$shared" "$db" "$dry" "$name"
  done < "$branches"
  # Metadata prune last: git reads an unreadable worktree directory as gone
  # and would drop its entry, orphaning a branch this run could not judge.
  if (( ! dry )); then
    if (( unenterable )); then
      warn "skipping \`git worktree prune\`: a worktree could not be entered; restore access and re-run"
    elif ! git -C "$shared" worktree prune --expire now 2>"$ERRFILE"; then
      # Recorded, not merely warned: the run continues, the exit stays non-zero.
      row failed "git worktree prune" "" "failed: $(tr '\n' ' ' < "$ERRFILE") — stale metadata may remain"
    fi
  fi
  if ! rm -f "$inventory" "$branches"; then
    warn "could not remove temp inventories ${inventory} ${branches} — remove them by hand"
  fi

  local rc=0
  python3 - "$abs_shared" "$db" "$dry" "$ROWS" <<'PY' || rc=$?
import json, sys
shared, db, dry, rows_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
result = {"shared": shared, "default_branch": db, "dry_run": dry, "worktrees_removed": [], "worktrees_kept": [],
          "branches_deleted": [], "branches_kept": [], "failed": []}
with open(rows_path, "rb") as handle:
    fields = handle.read().decode("utf-8", "surrogateescape").split("\0")
if fields and fields[-1] == "":
    fields.pop()
if len(fields) % 4:
    sys.stderr.write("prune-worktrees: decision rows are malformed; report this as a bug\n")
    sys.exit(2)
for index in range(0, len(fields), 4):
    kind, target, branch, reason = fields[index:index + 4]
    if True:
        if kind == "removed":
            result["worktrees_removed"].append({"path": target, "branch": branch})
        elif kind == "kept":
            result["worktrees_kept"].append({"path": target, "branch": branch or None, "reason": reason})
        elif kind == "branch-deleted":
            result["branches_deleted"].append(branch)
        elif kind == "branch-kept":
            result["branches_kept"].append({"branch": branch, "reason": reason})
        else:
            result["failed"].append({"target": target, "error": reason})
print(json.dumps(result, sort_keys=True))
sys.exit(2 if result["failed"] else 0)
PY
  return "$rc"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
