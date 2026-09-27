#!/usr/bin/env bash
# Remove the spent worker worktrees and local branches a round left behind;
# report the ones holding work that exists nowhere else, never touch them.
#
# Every Herdr round provisions worktrees under the worktree root
# (`provision-worktree.sh`) and the foreman removes the task's own worktree after
# its merge (`rules/agent-worktree-isolation.md` Cleanup). Review, test and
# verify worktrees from earlier rounds, and the local branches a removed
# worktree leaves behind, accumulate until someone notices `git worktree list`
# scrolling. Which of them are safe to remove is one right answer per input,
# so the decision lives here (`rules/script-delegation.md`).
#
# Decision predicate — a worktree under the worktree root, other than the
# shared checkout, not locked and not on origin's default branch, is:
#   * REMOVED (its branch deleted) when IDLE for IDLE_HOURS, clean, holding no
#     other repository's checkout, and either its branch is an ancestor of the
#     commit origin's default branch points at right now (`git ls-remote`,
#     ORIGIN_DEFAULT) or its HEAD is an ancestor of a branch tip origin holds
#     right now (`git ls-remote --heads origin`, ORIGIN_TIPS), detached
#     included. Removal is plain `git worktree remove`, never forced: git
#     refuses a tree that turned dirty.
#   * KEPT and reported as `dirty` (with its changed-file count) or `unpushed`
#     (with the count of commits no origin branch holds) when idle but holding
#     work that exists nowhere else, with the one-line command for the
#     operator. Nothing here pushes, commits, stashes or deletes that work.
#   * KEPT for every other reason below.
# A worktree holding another repository's checkout is KEPT: a gitlink found
# from the index (never .gitmodules) whose checkout changed (submodule-dirty)
# or is populated (submodule), or any other .git anywhere below it, found by
# walking the whole tree, ignored and untracked directories alike
# (nested-repo); a directory the walk cannot read keeps it.
# "Clean" is `git status --porcelain --untracked-files=all` empty — untracked
# files count as dirty whatever `status.showUntrackedFiles` says; ignored files
# do not, they are reproducible by the ignore's own claim.
# "IDLE for N hours" is both: no process of this user has its cwd inside the
# worktree (`lsof -F pn0`; a missing or failing probe, an incomplete listing —
# a warning, a process with no cwd name, a cwd lsof could not read — and a
# path lsof cannot print faithfully all keep every worktree, reason
# idle-unknown), and the newest mtime among the worktree directory, its own
# gitdir's HEAD, index and logs/HEAD, and every modified tracked or untracked
# non-ignored file is at least N hours old (a worktree holding more such files
# than ACTIVITY_FILE_LIMIT is never idle). Every read runs with
# --no-optional-locks, so judging never rewrites the index.
# Immediately before each removal (and before a dry run's preview of one),
# the path is re-proven to resolve to itself, HEAD, branch tip, status, age
# and a fresh process probe are re-read, and the removal proof is re-derived
# from origin as it is now, which branch origin's HEAD names included; any
# change keeps the worktree (reason changed). IDLE_HOURS and
# ACTIVITY_FILE_LIMIT are constants beside the functions that use them.
# A git command that talks to origin (fetch, ls-remote, set-head) that fails
# is reported by exit code and the command to rerun, never by its own message,
# which can carry the remote URL with credentials (`network_failure`).
# A fetch or default-branch lookup that fails is a precondition failure, never
# a judgment from stale refs. Every removal is restorable from origin:
# `git worktree add <path> <head>`.
# A local branch with no worktree is DELETED iff it is not the default branch
# and either is an ancestor of origin's default branch or has its tip held by
# a branch origin holds, the proof re-read with `ls-remote` immediately before
# the deletion (and before a dry run's preview). That check is the safety, and
# it judges a captured commit rather than a name that can move. A branch with
# unpushed commits is KEPT: once idle for IDLE_HOURS (its tip commit and its
# newest reflog entry) it is reported as `unpushed` with its commit count, age
# and push command; before that as `not-idle`.
# `update-ref` carries no checked-out-worktree guard of its own, and the
# inventory is a snapshot, so occupancy is re-read from git either side of
# every deletion: a branch a worktree holds is KEPT with reason checked-out,
# and one claimed inside the deletion's own window is restored at the commit
# it was deleted from.
# Deletion is then `git update-ref -d refs/heads/<branch> <that commit>`,
# git's compare-and-delete: it removes the branch only while it still points
# at the commit just proved merged, so a commit landing mid-run keeps the
# branch instead of being force-deleted. `branch -d` would re-derive the
# safety against the local default, which may lag origin's, and `branch -D`
# would skip it entirely; neither is atomic with the check. Removal is `git worktree remove`,
# never `rm -rf`. Stale worktree metadata is pruned (`git worktree prune
# --expire now`) after every worktree decision and before the branch pass, so
# a confirmed-gone entry's merged branch goes in the same run; the prune is
# skipped when any worktree could not be entered, since git would read an
# unreadable directory as gone and drop its entry. Nothing here pushes to
# origin; a dry run still fetches (without --prune), reads origin's default
# branch with `ls-remote --symref` instead of rewriting origin/HEAD, and skips
# the metadata prune, so its decisions are current and .git is otherwise
# untouched.
#
# Contract:
#   argv  : <shared-checkout> [--dry-run]
#           --dry-run reports the same decisions and removes nothing. It still
#           fetches, so its answer is current: remote-tracking refs, FETCH_HEAD
#           and the object database move as any fetch moves them. No worktree,
#           branch, config or metadata entry changes.
#   stdout: one JSON object —
#           {"shared":"<abs>","default_branch":"<name>","dry_run":bool,
#            "worktrees_removed":[{"path","branch","head"}],
#            "worktrees_kept":[{"path","branch","reason"[,"lock_reason"]
#                               [,"head","age_hours","dirty_files"|"unpushed_commits","command"]}],
#            "branches_deleted":["<name>"],
#            "branches_kept":[{"branch","reason"[,"unpushed_commits","age_hours","command"]}],
#            "failed":[{"target","error"}]}
#           reason is one of: changed (it changed between judgment and
#           removal, or origin's proof went away), checked-out (a worktree
#           claimed the branch after the inventory was taken), default-branch,
#           dirty (idle, uncommitted work; with head, age_hours,
#           dirty_files and command),
#           idle-unknown (the process probe could not run, returned an
#           incomplete listing, or cannot print this path faithfully), in-use
#           (a process works inside it), locked (with its lock_reason),
#           nested-repo, not-idle (activity within IDLE_HOURS), outside-root,
#           prunable (its directory is gone; a live run's metadata prune
#           removes it), submodule, submodule-dirty, unpushed (idle, commits
#           origin holds nowhere; with unpushed_commits, age_hours and
#           command, and for a worktree its head too).
#   stderr: diagnostics only.
#   exit  : 0 every decision applied (or previewed),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             no origin, fetch failed, default branch unresolvable, worktree
#             or branch inventory unreadable) — no JSON, nothing decided,
#           2 at least one check, removal or deletion failed; the rest still
#             ran and `failed` names each one.
#   env   : WORKTREE_ROOT overrides the worktree root (default
#           $HOME/.worktrees); the tests point it at a temp dir.
#           PRUNE_IDLE_HOURS / PRUNE_ACTIVITY_FILE_LIMIT override the
#           constants, PRUNE_NOW (epoch seconds) the clock, PRUNE_LSOF the probe.
set -euo pipefail

WORKDIR=""
ERRFILE=""
ROWS=""

warn() { printf 'prune-worktrees: %s\n' "$1" >&2; }

# After a git command that talks to origin fails: replace its stderr in
# ERRFILE with the exit code and the command to rerun. Its own message can
# carry the remote URL, credentials included, so it is never relayed.
network_failure() { # <exit> <shared> <git args...>
  local rc="$1" shared="$2"
  shift 2
  # shellcheck disable=SC2016  # The backticks are literal text in the message, not a command substitution.
  printf '`git %s` exited %s; run `git -C %s %s` to see why (its output is not relayed: it can carry the remote URL with credentials)\n' \
    "$*" "$rc" "$shared" "$*" > "$ERRFILE"
}

cleanup() {
  local f
  if [[ -n "$WORKDIR" ]] && ! rm -rf "$WORKDIR"; then
    warn "could not remove the temporary directory ${WORKDIR} — remove it by hand"
  fi
  for f in "$CWD_FILE" "$ORIGIN_TIPS"; do
    if [[ -n "$f" ]] && ! rm -f "$f"; then
      warn "could not remove temp file ${f} — remove it by hand"
    fi
  done
  return 0
}

# Append one decision row: <kind> <target> <branch> <reason>, NUL-delimited
# so a path or diagnostic holding a tab or newline cannot shift the fields.
row() { # <kind> <target> <branch> <reason> [head] [extra]
  printf '%s\0%s\0%s\0%s\0%s\0%s\0' "$1" "$2" "$3" "$4" "${5:-}" "${6:-}" >> "$ROWS"
  # A failure is a diagnostic on stderr as well as a JSON row
  # (rules/script-delegation.md Script Requirements).
  if [[ "$1" == failed ]]; then
    warn "${2}: ${4} — inspect it by hand; nothing else was skipped on its account"
  fi
}

# 0 when <ref> exists, 1 when it is absent; any other show-ref exit is a tool
# failure, warned about and returned as 2 so no fallback runs on top of it.
ref_exists() { # <shared> <ref>
  local rc=0
  git -C "$1" show-ref --verify --quiet "$2" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0|1) return "$rc" ;;
    *) warn "\`git show-ref --verify ${2}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run it in ${1} to see why, then re-run"; return 2 ;;
  esac
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
      network_failure "$rc" "$1" ls-remote --symref origin HEAD
      warn "$(cat "$ERRFILE")"
      return 1
    fi
    db="$(printf '%s\n' "$sym" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
    if [[ -n "$db" ]]; then
      # The remote named its default; a missing local origin/<name> is a
      # refspec or fetch problem, never a reason to guess main or master.
      ref_exists "$1" "refs/remotes/origin/${db}" || rc=$?
      case "$rc" in
        0) printf '%s' "$db"; return 0 ;;
        1) warn "origin reports default branch '${db}' but refs/remotes/origin/${db} is absent after the fetch — check the fetch refspec"; return 1 ;;
        *) return 1 ;;
      esac
    fi
  else
    # The full target, not --short: a local branch named origin/<x> makes the
    # short form ambiguous and git renders it as remotes/origin/<x>.
    db="$(git -C "$1" symbolic-ref --quiet refs/remotes/origin/HEAD 2>"$ERRFILE")" || rc=$?
    case "$rc" in
      0) if [[ "$db" != refs/remotes/origin/* ]]; then
           warn "origin/HEAD points at '${db}', not a refs/remotes/origin/ ref — run \`git remote set-head origin --auto\`"; return 1
         fi
         db="${db#refs/remotes/origin/}"
         # origin/HEAD was just rewritten from the remote; a dangling one is a
         # refspec or fetch problem, never a reason to guess main or master.
         rc=0; ref_exists "$1" "refs/remotes/origin/${db}" || rc=$?
         case "$rc" in
           0) printf '%s' "$db"; return 0 ;;
           1) warn "origin/HEAD names '${db}' but refs/remotes/origin/${db} is absent after the fetch — check the fetch refspec"; return 1 ;;
           *) return 1 ;;
         esac ;;
      1) ;;  # origin/HEAD is simply absent: fall back to the conventional names
      *) warn "\`git symbolic-ref refs/remotes/origin/HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`git -C ${1} remote set-head origin --auto\`, then re-run"; return 1 ;;
    esac
  fi
  local cand
  for cand in main master; do
    rc=0; ref_exists "$1" "refs/remotes/origin/$cand" || rc=$?
    case "$rc" in 0) printf '%s' "$cand"; return 0 ;; 1) ;; *) return 1 ;; esac
  done
  return 1
}

# Echo merged|unmerged for <commit> against refs/remotes/origin/<default>,
# or return 2 on a tool failure. The caller passes the commit it captured, so
# a commit landing after the capture cannot become the judged tip.
# `merge-base --is-ancestor` exits 1 for "not an ancestor" and
# anything else for an invalid ref or repository error; collapsing both into
# "unmerged" would hide the failure behind a kept row
# (rules/error-handling.md Shell Error Handling).
#: The commit origin's default branch points at, as origin reported it when
#: this run started (`origin_default_tip`). Merged-ness is judged against it,
#: never a local remote-tracking ref, so dry and live runs see the same origin.
ORIGIN_DEFAULT=""

# Echo the commit origin's <default> branch points at right now, read with
# ls-remote; return 1 when it cannot be read, 3 when origin names a commit
# this repository does not hold (origin moved since the fetch).
origin_default_tip() { # <shared> <default>
  local out rc=0 sha
  out="$(git -C "$1" ls-remote origin "refs/heads/$2" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    network_failure "$rc" "$1" ls-remote origin "refs/heads/$2"
    return 1
  fi
  sha="${out%%[[:space:]]*}"
  if [[ -z "$sha" ]]; then printf 'origin has no branch %s\n' "$2" > "$ERRFILE"; return 1; fi
  if ! git -C "$1" cat-file -e "${sha}^{commit}" 2>"$ERRFILE"; then
    printf 'origin %s points at %s, which this repository does not hold\n' "$2" "$sha" > "$ERRFILE"
    return 3
  fi
  printf '%s' "$sha"
}

ancestry() { # <shared> <commit> <default>
  local rc=0
  # Against the commit origin reported, not a ref name: a tag or a local
  # branch named like the default can never stand in for it.
  git -C "$1" merge-base --is-ancestor "$2" "$ORIGIN_DEFAULT" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0) printf 'merged' ;;
    1) printf 'unmerged' ;;
    *) return 2 ;;
  esac
  return 0
}

# Echo the commit refs/heads/<branch> points at, or return 1 on any failure.
branch_tip() { # <shared> <branch>
  local out rc=0
  out="$(git -C "$1" rev-parse --verify --quiet "refs/heads/$2^{commit}" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$out" ]]; then
    return 1
  fi
  printf '%s' "$out"
}

# Echo 1 when some worktree currently has <branch> checked out and 0 when none
# does; return 1 when the inventory cannot be read. `update-ref -d` carries
# none of git's own checked-out-worktree guard, and the run's inventory is a
# snapshot: a `worktree add` claiming the branch after it was taken is
# invisible to `seen_branches` (#410). So occupancy is re-read at the deletion.
branch_checked_out() { # <shared> <branch>
  local listing rc=0 field found=0
  # The caller builds its failure row from ERRFILE, so a bare `return 1` here
  # would report the occupancy read as failing for whatever the previous
  # command left there (#426).
  listing="$(mktemp 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$listing" ]]; then
    printf 'mktemp failed (exit %s): %s\n' "${rc:-0}" "$(tr '\n' ' ' < "$ERRFILE")" > "$ERRFILE"
    return 1
  fi
  if ! git -C "$1" worktree list --porcelain -z >"$listing" 2>"$ERRFILE"; then
    if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
    return 1
  fi
  while IFS= read -r -d '' field || [[ -n "$field" ]]; do
    if [[ "$field" == "branch refs/heads/$2" ]]; then found=1; break; fi
  done < "$listing"
  if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
  printf '%s' "$found"
}

# Delete <branch> only if it still points at <tip>, atomically: `update-ref -d`
# with an old value is git's compare-and-delete, so no commit can land between
# the check and the deletion the way a read-then-`branch -D` allows (#405).
# The ancestry proof is the safety `branch -d` would otherwise re-derive
# against the local default, which may lag origin's. Occupancy is re-read
# either side of it: before, so a worktree created since the inventory is
# seen; after, so one created inside that window is put back rather than left
# checked out on a branch that no longer exists (#410).
delete_branch() { # <shared> <branch> <tip>  -> 0 deleted, 1 moved, 2 git refused, 3 deleted but config left, 4 occupancy unreadable, 5 checked out, 6 claimed mid-run and restored, 7 claimed mid-run and not restored, 8 deleted and post-deletion occupancy unreadable, 9 deleted and its config section left to a branch recreated since
  local rc=0 now refusal occupied
  if ! occupied="$(branch_checked_out "$1" "$2")"; then
    return 4
  fi
  if [[ "$occupied" == 1 ]]; then
    return 5
  fi
  git -C "$1" update-ref -d "refs/heads/$2" "$3" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    # Hold git's own words: the re-read below truncates ERRFILE, and a
    # refusal the caller reports without them is undiagnosable.
    refusal="$(tr '\n' ' ' < "$ERRFILE")"
    if now="$(branch_tip "$1" "$2")" && [[ "$now" != "$3" ]]; then
      return 1
    fi
    printf '%s' "$refusal" > "$ERRFILE"
    return 2
  fi
  # The window between the check above and the deletion is small, not empty.
  # A worktree that claimed the branch inside it is now checked out on a ref
  # that is gone, so the branch goes back at the commit it was deleted from —
  # `update-ref` with an empty old value creates it only while it is still
  # absent, so a racing `worktree add -b` that made its own is left alone.
  # A failed read here is not "no worktree holds it": returning to the config
  # probe below would overwrite this diagnostic and report a clean deletion
  # whose safety check never ran (rules/error-handling.md Shell Error
  # Handling — an expected non-result is not a tool failure).
  if ! occupied="$(branch_checked_out "$1" "$2")"; then
    return 8
  fi
  if [[ "$occupied" == 1 ]]; then
    if git -C "$1" update-ref "refs/heads/$2" "$3" "" 2>"$ERRFILE"; then
      return 6
    fi
    return 7
  fi
  # `update-ref` leaves the branch config `branch -D` would have removed.
  # Whether there is any is read first: `--remove-section` exits 128 for a
  # missing section and for a malformed config alike, so the exit code alone
  # cannot tell an expected non-result from a failure.
  local keys=""
  rc=0
  keys="$(git -C "$1" config --get-regexp '^branch\.' 2>"$ERRFILE")" || rc=$?
  case "$rc" in
    0) ;;
    1) return 0 ;;  # no branch.* config at all
    *) return 3 ;;
  esac
  # Section equality, not a substring: `branch.foo.` also prefixes
  # `branch.foo.bar.remote`, which belongs to the branch `foo.bar`. Reading
  # that as `foo`'s config makes `--remove-section branch.foo` fail on a
  # section that was never there, and the run reports a cleanup failure it
  # invented (#410). A config key is `branch.<name>.<key>` and `<name>` may
  # hold dots, so the name is what sits between the first dot and the last.
  local keyline key section found=0
  while IFS= read -r keyline; do
    key="${keyline%% *}"
    [[ "$key" == branch.*.* ]] || continue
    section="${key#branch.}"
    section="${section%.*}"
    if [[ "$section" == "$2" ]]; then found=1; break; fi
  done <<<"$keys"
  if (( ! found )); then
    return 0
  fi
  # The section may have been recreated since the deletion: `worktree add
  # --track -b` writes a fresh `branch.<name>.*` for the NEW branch, and
  # removing it then deletes configuration belonging to a live checkout (#426).
  # Occupancy is re-read immediately before the removal, the same guard the
  # deletion itself takes.
  local now_held
  if ! now_held="$(branch_checked_out "$1" "$2")"; then
    # The deletion already happened; 4 would tell the caller it did not.
    return 8
  fi
  if [[ "$now_held" == 1 ]]; then
    return 9
  fi
  rc=0
  git -C "$1" config --remove-section "branch.$2" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    return 3
  fi
  return 0
}

# Report a branch deletion that followed a worktree removal.
report_branch_delete() { # <branch> <tip> <delete_branch rc>
  local branch="$1" tip="$2"
  case "$3" in
    0) ;;
    1) row failed "$branch" "$branch" "branch ${branch} moved after its ancestry check and was left alone; its worktree is already removed, so re-run to judge the new tip" ;;
    3) row failed "$branch" "$branch" "${branch} is deleted but its branch.${branch} config could not be cleaned up: $(tr '\n' ' ' < "$ERRFILE") — check and remove it by hand" ;;
    4) row failed "$branch" "$branch" "could not re-read which worktrees hold ${branch} after its own was removed, so it was left alone: $(tr '\n' ' ' < "$ERRFILE")" ;;
    5) row failed "$branch" "$branch" "another worktree claimed ${branch} after its own was removed, so it was left alone; re-run once that worktree is gone" ;;
    6) row failed "$branch" "$branch" "another worktree claimed ${branch} while it was being deleted; the branch was restored at ${tip} and that worktree is intact — re-run once it is gone" ;;
    7) row failed "$branch" "$branch" "another worktree claimed ${branch} while it was being deleted and it could not be restored: $(tr '\n' ' ' < "$ERRFILE") — inspect that worktree by hand" ;;
    8) row failed "$branch" "$branch" "${branch} is deleted and whether a worktree claimed it meanwhile could not be read: $(tr '\n' ' ' < "$ERRFILE") — check \`git worktree list\` and restore ${branch} at ${tip} if one holds it" ;;
    9) row failed "$branch" "$branch" "${branch} is deleted, and a worktree recreated a branch of that name before its config could be cleaned up — branch.${branch} belongs to that live branch and was left untouched; remove nothing by hand" ;;
    *) row failed "$branch" "$branch" "deleting ${branch} failed after the worktree was removed: $(tr '\n' ' ' < "$ERRFILE")" ;;
  esac
}

#: `ok` once CWD_FILE holds every cwd of this user's processes, `failed` when
#: the probe could not run, empty before a probe. `reprobe` clears it so a
#: pre-removal recheck reads the processes as they are now.
CWD_STATE=""
CWD_FILE=""

reprobe() { CWD_STATE=""; }

# 0 = some live process of this user has its cwd inside <real>, 1 = none does,
# 2 = unknown (probe missing or failed, or <real> is a path lsof cannot report
# faithfully). Unknown is never read as idle.
# lsof escapes a newline or another control byte in a name as text (`\n`), so
# a path holding one, a backslash, or a non-ASCII byte cannot be matched
# against its output without guessing; such a worktree is never judged idle.
# The listing is NUL-framed (`-F pn0`), so one name is one field.
in_use() { # <real-path>
  if [[ "$1" == *\\* ]] || ! LC_ALL=C python3 -c 'import sys; sys.exit(0 if all(0x20 <= b <= 0x7e for b in sys.argv[1].encode("utf-8", "surrogateescape")) else 1)' "$1"; then
    return 2
  fi
  if [[ -z "$CWD_STATE" ]]; then
    CWD_STATE=failed
    local lsof_bin="${PRUNE_LSOF:-lsof}" uid
    if ! command -v "$lsof_bin" >/dev/null; then
      warn "${lsof_bin} not found on PATH — cannot tell whether a worktree is in use, so none is judged idle; install lsof"
    elif ! uid="$(id -u)"; then
      warn "id -u failed — cannot scope the process probe, so no worktree is judged idle; run \`id -u\` to see why, then re-run"
    elif [[ -z "$CWD_FILE" ]] && ! CWD_FILE="$(mktemp)"; then
      CWD_FILE=""
      warn "mktemp failed — cannot hold the process probe, so no worktree is judged idle; make ${TMPDIR:-/tmp} writable, then re-run"
    elif ! "$lsof_bin" -a -u "$uid" -d cwd -F pn0 >"$CWD_FILE" 2>"$ERRFILE"; then
      warn "\`${lsof_bin} -a -u ${uid} -d cwd -F pn0\` failed: $(tr '\n' ' ' < "$ERRFILE") — no worktree is judged idle; run it by hand to see why, then re-run"
    elif ! complete_cwd_listing "$CWD_FILE" "$ERRFILE"; then
      # Exit 0 is not a complete answer: a warning other than a mount lsof
      # could not stat, or a live process without a readable cwd name, leaves
      # some process's cwd unknown, and that process could be inside any
      # worktree.
      warn "\`${lsof_bin} -a -u ${uid} -d cwd -F pn0\` returned an incomplete listing — no worktree is judged idle; run it by hand to see which process it could not read"
    else
      if [[ -s "$ERRFILE" ]]; then
        warn "${lsof_bin} warned about a mount it could not stat; process cwds are still complete: $(tr '\n' ' ' < "$ERRFILE")"
      fi
      CWD_STATE=ok
    fi
  fi
  [[ "$CWD_STATE" == ok ]] || return 2
  local field cwd
  while IFS= read -r -d '' field || [[ -n "$field" ]]; do
    # A process set ends with a newline after its last NUL; it leads the
    # next field.
    field="${field#$'\n'}"
    [[ "$field" == n* ]] || continue
    cwd="${field#n}"
    if [[ "$cwd" == "$1" || "$cwd" == "$1"/* ]]; then return 0; fi
  done < "$CWD_FILE"
  return 1
}

# 0 when the NUL-framed lsof listing names a readable cwd for every process
# still alive, and lsof's stderr holds nothing but its warning about a mount
# it could not stat (the cwd records stay complete; the device number comes
# from the mount table). 1 otherwise. A process that exited while lsof read
# it holds no cwd anywhere, so its unreadable record is no gap; one still
# alive, or whose liveness cannot be read, is.
complete_cwd_listing() { # <listing-file> <stderr-file>
  python3 - "$1" "$2" <<'PY'
import os
import re
import sys

MOUNT_WARNING = (
    re.compile(rb"^lsof: WARNING: can't stat\(\) \S+ file system .*$"),
    re.compile(rb"^\s*Output information may be incomplete\.$"),
    re.compile(rb'^\s*assuming "dev=[0-9a-fA-Fx]+" from mount table$'),
)

with open(sys.argv[2], "rb") as handle:
    for line in handle.read().splitlines():
        if line.strip() and not any(p.match(line) for p in MOUNT_WARNING):
            sys.exit(1)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


with open(sys.argv[1], "rb") as handle:
    fields = [f.lstrip(b"\n") for f in handle.read().split(b"\0")]
gaps = []
pid = b""
named = None
for field in fields:
    if field.startswith(b"p"):
        if named is False:
            gaps.append(pid)
        pid, named = field[1:], False
    elif field.startswith(b"n"):
        if re.search(rb"\((readlink|stat|lstat): [^)]*\)$", field) or b"Permission denied" in field:
            gaps.append(pid)
        named = True
if named is False:
    gaps.append(pid)
for gap in gaps:
    if not gap.isdigit() or alive(int(gap)):
        sys.exit(1)
sys.exit(0)
PY
}

#: The idle clock stats at most this many modified or untracked files; a
#: worktree holding more is never judged idle.
ACTIVITY_FILE_LIMIT="${PRUNE_ACTIVITY_FILE_LIMIT:-20000}"

# Echo whole hours since the newest activity in the worktree at <real>: the
# worktree directory, its own gitdir's HEAD, index and logs/HEAD, and every
# modified tracked or untracked non-ignored file. Returns 1 when that cannot
# be read. PRUNE_NOW (epoch seconds) replaces the clock for tests.
idle_hours() { # <real-path>
  local gitdir
  if ! gitdir="$(git -C "$1" rev-parse --absolute-git-dir 2>"$ERRFILE")"; then
    return 1
  fi
  python3 - "$1" "$gitdir" "${PRUNE_NOW:-}" "$ACTIVITY_FILE_LIMIT" 2>"$ERRFILE" <<'PY'
import os
import subprocess
import sys
import time

path, gitdir, now, limit = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
now = float(now) if now else time.time()
candidates = [path, os.path.join(gitdir, "HEAD"), os.path.join(gitdir, "index"), os.path.join(gitdir, "logs", "HEAD")]
listed = subprocess.run(["git", "--no-optional-locks", "-C", path, "ls-files", "-z", "-m", "-o", "--exclude-standard"],
                        capture_output=True)
if listed.returncode != 0:
    sys.stderr.write("git ls-files failed: {}".format(listed.stderr.decode("utf-8", "replace")))
    sys.exit(1)
files = [name for name in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if name]
if len(files) > limit:
    sys.stderr.write("{} modified or untracked files exceed the idle-clock limit of {}".format(len(files), limit))
    sys.exit(1)
candidates += [os.path.join(path, name) for name in files]
stamps = []
for candidate in candidates:
    try:
        stamps.append(os.lstat(candidate).st_mtime)
    except FileNotFoundError:
        continue
if not stamps:
    sys.exit(1)
print(int(max(0.0, now - max(stamps)) // 3600))
PY
}

# Snapshot what the removal decision rests on, as one line: HEAD, the branch
# tip, and the porcelain status. Returns 1 when any read fails.
worktree_state() { # <shared> <real> <branch|"">
  local head tip="" status
  head="$(git -C "$2" rev-parse --verify --quiet 'HEAD^{commit}' 2>"$ERRFILE")" || return 1
  if [[ -n "$3" ]]; then tip="$(branch_tip "$1" "$3")" || return 1; fi
  status="$(git --no-optional-locks -C "$2" status --porcelain --untracked-files=all 2>"$ERRFILE")" || return 1
  printf '%s %s %s' "$head" "$tip" "$status"
}

# Immediately before a removal: 0 when the worktree is unchanged since it was
# judged and still idle for <window> hours with no process inside; otherwise
# 1 with RECHECK_WHY set.
RECHECK_WHY=""
recheck() { # <shared> <real> <branch|""> <state-when-judged> <window-hours>
  local now_state age rc=0 real
  RECHECK_WHY=""
  # The path still names this same directory, not a symlink swapped in.
  if [[ -L "$2" ]] || ! real="$(cd "$2" 2>"$ERRFILE" && pwd -P)" || [[ "$real" != "$2" ]]; then
    RECHECK_WHY="its path no longer resolves to itself"; return 1
  fi
  if ! now_state="$(worktree_state "$1" "$2" "$3")"; then
    RECHECK_WHY="its state could not be re-read: $(tr '\n' ' ' < "$ERRFILE")"; return 1
  fi
  if [[ "$now_state" != "$4" ]]; then RECHECK_WHY="its HEAD, branch tip or status changed"; return 1; fi
  if ! age="$(idle_hours "$2")"; then RECHECK_WHY="its activity age could not be re-read"; return 1; fi
  if (( age < $5 )); then RECHECK_WHY="it was written to ${age}h ago"; return 1; fi
  reprobe
  in_use "$2" || rc=$?
  case "$rc" in
    1) return 0 ;;
    0) RECHECK_WHY="a process is now working inside it" ;;
    *) RECHECK_WHY="the process probe could not run" ;;
  esac
  return 1
}

#: File of the branch tips origin holds right now (`git ls-remote --heads`),
#: restricted to commits present locally. Live and dry runs both judge
#: reachability against it, so a branch deleted on origin since the last
#: fetch counts for neither.
ORIGIN_TIPS=""

# Record origin's current branch tips in ORIGIN_TIPS. Returns 1 on failure.
read_origin_tips() { # <shared>
  local listing
  if [[ -z "$ORIGIN_TIPS" ]] && ! ORIGIN_TIPS="$(mktemp 2>"$ERRFILE")"; then ORIGIN_TIPS=""; return 1; fi
  if ! listing="$(mktemp 2>"$ERRFILE")"; then return 1; fi
  local rc=0
  git -C "$1" ls-remote --heads origin >"$listing" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    network_failure "$rc" "$1" ls-remote --heads origin
    if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
    return 1
  fi
  local ok=0
  if python3 - "$1" "$listing" "$ORIGIN_TIPS" 2>"$ERRFILE" <<'PY'
import subprocess
import sys

shared, listing, out = sys.argv[1:4]
with open(listing, encoding="utf-8", errors="surrogateescape") as handle:
    shas = sorted({line.split("\t", 1)[0] for line in handle if line.strip()})
check = subprocess.run(["git", "-C", shared, "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                       input="".join(sha + "\n" for sha in shas).encode("ascii"), capture_output=True)
if check.returncode != 0:
    sys.stderr.write(check.stderr.decode("utf-8", "surrogateescape"))
    sys.exit(1)
present = [line.split(" ")[0] for line in check.stdout.decode("utf-8", "surrogateescape").splitlines()
           if line.endswith(" commit")]
with open(out, "w", encoding="utf-8") as handle:
    handle.write("".join(sha + "\n" for sha in present))
PY
  then ok=1; fi
  if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
  (( ok ))
}

# 0 when <commit> is an ancestor of a branch tip origin holds now, 1 when
# none, 2 on a tool failure.
reachable_remotely() { # <shared> <commit>
  local -a tips=()
  local tip
  while IFS= read -r tip; do [[ -n "$tip" ]] && tips+=("^${tip}"); done < "$ORIGIN_TIPS"
  (( ${#tips[@]} )) || return 1
  local out
  # Empty output: every commit <commit> reaches is also reached by a tip.
  if ! out="$(git -C "$1" rev-list -n 1 "$2" "${tips[@]}" 2>"$ERRFILE")"; then
    return 2
  fi
  [[ -z "$out" ]]
}

# Re-derive the removal proof immediately before a removal: 0 when <tip> is
# still merged into origin's default (<mode> merged) or <head> still held by an
# origin ref (<mode> reachable), 1 when the proof is gone, 2 on a failure.
# Echo the branch origin's HEAD names now; 1 (ERRFILE says why) when it
# cannot be read.
origin_head_name() { # <shared>
  local out rc=0 name
  out="$(git -C "$1" ls-remote --symref origin HEAD 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$1" ls-remote --symref origin HEAD; return 1; fi
  name="$(printf '%s\n' "$out" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
  if [[ -z "$name" ]]; then printf 'origin reports no default branch\n' > "$ERRFILE"; return 1; fi
  printf '%s' "$name"
}

proof_holds() { # <shared> <mode> <tip> <head> <default>
  if [[ "$2" == merged ]]; then
    # Origin's default as it is now: a force-push since the run started can
    # drop the merge, and a new default need not hold it at all.
    local now_tip now_db trc=0
    now_db="$(origin_head_name "$1")" || return 2
    [[ "$now_db" == "$5" ]] || return 1
    now_tip="$(origin_default_tip "$1" "$5")" || trc=$?
    case "$trc" in
      0) ;;
      3) return 1 ;;
      *) return 2 ;;
    esac
    git -C "$1" merge-base --is-ancestor "$3" "$now_tip" 2>"$ERRFILE" || trc=$?
    case "$trc" in
      0) return 0 ;;
      1) return 1 ;;
      *) return 2 ;;
    esac
  else
    # Origin as it is now, not as it was when the run started.
    read_origin_tips "$1" || return 2
    reachable_remotely "$1" "$4"
  fi
}

# Echo a keep reason when the worktree at <real> holds another repository's
# checkout that a removal would destroy, else nothing:
#   submodule-dirty  a gitlink (mode 160000, found from the index, never from
#                    .gitmodules) whose checkout has a new commit, changes or
#                    untracked files
#   submodule        a populated gitlink checkout; git refuses to move or
#                    remove a worktree holding one
#   nested-repo      any other .git anywhere below the worktree, in untracked
#                    or ignored directories alike, found by walking the tree
# Returns 1 on a tool failure.
nested_checkouts() { # <real>
  python3 - "$1" 2>"$ERRFILE" <<'PY'
import os
import subprocess
import sys

path = sys.argv[1]


def git(*args):
    run = subprocess.run(["git", "--no-optional-locks", "-C", path, *args], capture_output=True)
    if run.returncode != 0:
        sys.stderr.write(run.stderr.decode("utf-8", "replace"))
        sys.exit(1)
    return run.stdout.decode("utf-8", "surrogateescape")


status = git("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignore-submodules=none")
for entry in status.split("\0"):
    parts = entry.split(" ")
    if parts[0] in ("1", "2") and len(parts) > 2 and parts[2].startswith("S") and parts[2] != "S...":
        print("submodule-dirty")
        sys.exit(0)
gitlinks = [line.split("\t", 1)[1] for line in git("ls-files", "-s", "-z").split("\0")
            if line.startswith("160000 ")]
if any(os.path.exists(os.path.join(path, link, ".git")) for link in gitlinks):
    print("submodule")
    sys.exit(0)
# Any other .git below the worktree root is an embedded repository: walk the
# whole tree, ignored and untracked directories included, never trusting git's
# own listing (it collapses an untracked directory and skips ignored ones).
own = os.path.join(path, ".git")
def unreadable(error):
    # A directory the walk cannot read could hold an embedded repository.
    sys.stderr.write("cannot read {}: {}".format(error.filename, error.strerror))
    sys.exit(1)


for current, dirs, files in os.walk(path, followlinks=False, onerror=unreadable):
    if ".git" in dirs or ".git" in files:
        if os.path.join(current, ".git") != own:
            print("nested-repo")
            sys.exit(0)
    dirs[:] = [d for d in dirs if d != ".git"]
PY
}

# Decide one worktree; emits a row and performs the removal unless dry-run.
#: Set by `decide_worktree` when, and only when, it decided `prunable`. Git
#: keeps a locked entry's metadata through `worktree prune`, so a locked entry
#: whose directory is gone stays checked out and its branch must not be
#: released.
DECIDED_PRUNABLE=0

#: A worktree is judged only once idle this many hours: no git activity and
#: no process inside. Overridable for tests.
IDLE_HOURS="${PRUNE_IDLE_HOURS:-24}"

decide_worktree() { # <shared> <abs_root> <default> <dry-run 0|1> <path> <branch|""> <detached 0|1> <locked 0|1> [lock-reason]
  DECIDED_PRUNABLE=0
  local shared="$1" abs_root="$2" db="$3" dry="$4" path="$5" branch="$6" detached="$7" locked="$8" lock_reason="${9:-}"
  if [[ "$path" != "$abs_root"/* ]]; then
    row kept "$path" "$branch" outside-root; return 0
  fi
  if [[ -n "$branch" && "$branch" == "$db" ]]; then
    row kept "$path" "$branch" default-branch; return 0
  fi
  if (( locked )); then
    row kept "$path" "$branch" locked "" "$lock_reason"; return 0
  fi
  if [[ ! -d "$path" ]]; then
    # The caller releases this branch to the branch pass once the metadata
    # prune runs; an entry kept for any earlier reason never reaches here.
    DECIDED_PRUNABLE=1
    row kept "$path" "$branch" prunable; return 0
  fi
  # Age first, and every read below without optional locks: judging a
  # worktree must never refresh its index and reset its clock.
  local age="" age_ok=1
  if ! age="$(idle_hours "$path")"; then
    age_ok=0
    warn "cannot read the activity age of ${path}: $(tr '\n' ' ' < "$ERRFILE") — not judging it idle"
  fi
  local state
  if ! state="$(worktree_state "$shared" "$path" "$branch")"; then
    row failed "$path" "$branch" "cannot read HEAD, branch tip or status: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  local head="${state%% *}" rest="${state#* }" tip status
  tip="${rest%% *}"; status="${rest#* }"
  [[ -n "$tip" ]] || tip="$head"
  if (( ! age_ok )) || (( age < IDLE_HOURS )); then
    row kept "$path" "$branch" not-idle; return 0
  fi
  local rc=0
  in_use "$path" || rc=$?
  case "$rc" in
    0) row kept "$path" "$branch" in-use; return 0 ;;
    1) ;;
    *) row kept "$path" "$branch" idle-unknown; return 0 ;;
  esac
  local nested
  if ! nested="$(nested_checkouts "$path")"; then
    row failed "$path" "$branch" "cannot read its submodules or nested repositories, so it was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ -n "$nested" ]]; then
    row kept "$path" "$branch" "$nested"; return 0
  fi
  # Dirty: work git does not hold at all. Kept and reported, never removed.
  if [[ -n "$status" ]]; then
    local files; files="$(printf '%s\n' "$status" | grep -c .)"
    row kept "$path" "$branch" dirty "$head" "${files} ${age}"; return 0
  fi
  local merged="" mode=""
  if [[ -n "$branch" ]]; then
    if ! merged="$(ancestry "$shared" "$tip" "$db")"; then
      row failed "$path" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
    fi
    [[ "$merged" == merged ]] && mode=merged
  fi
  if [[ -z "$mode" ]]; then
    rc=0; reachable_remotely "$shared" "$head" || rc=$?
    case "$rc" in
      0) mode=reachable ;;
      1) ;;
      *) row failed "$path" "$branch" "cannot read which origin branches hold ${head}: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
    esac
  fi
  # Unpushed: commits origin does not hold. Kept and reported, never removed.
  if [[ -z "$mode" ]]; then
    local ahead
    if ! ahead="$(unpushed_count "$shared" "$head")"; then
      row failed "$path" "$branch" "cannot count its unpushed commits: $(tr '\n' ' ' < "$ERRFILE")"; return 0
    fi
    row kept "$path" "$branch" unpushed "$head" "${ahead} ${age}"; return 0
  fi
  # The same read-only recheck for a preview as for a removal: a dry run
  # promises only what the live run would do.
  if ! recheck "$shared" "$path" "$branch" "$state" "$IDLE_HOURS"; then
    row kept "$path" "$branch" changed; warn "kept ${path}: ${RECHECK_WHY}"; return 0
  fi
  # The proof is remote state a concurrent fetch can change: re-derive it last.
  rc=0; proof_holds "$shared" "$mode" "$tip" "$head" "$db" || rc=$?
  case "$rc" in
    0) ;;
    1) row kept "$path" "$branch" changed; warn "kept ${path}: origin no longer holds ${head}"; return 0 ;;
    *) row failed "$path" "$branch" "cannot re-verify that origin holds ${head}, so it was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
  esac
  remove_worktree "$shared" "$dry" "$path" "$branch" "$tip" "$mode" "$head" "$db"
  return 0
}

# Echo how many commits <commit> reaches that no branch origin holds reaches.
unpushed_count() { # <shared> <commit>
  local -a tips=()
  local tip
  while IFS= read -r tip; do [[ -n "$tip" ]] && tips+=("^${tip}"); done < "$ORIGIN_TIPS"
  git -C "$1" rev-list --count "$2" "${tips[@]+"${tips[@]}"}" 2>"$ERRFILE"
}

# Remove the worktree at <path> with plain `git worktree remove` (git itself
# refuses a tree that turned dirty), then delete its branch at <tip> once
# origin is proven, again, to hold it: the removal takes time, and a
# force-push inside it would leave the branch the last ref to its commits.
remove_worktree() { # <shared> <dry> <path> <branch> <tip> <mode> <head> <default>
  local shared="$1" dry="$2" path="$3" branch="$4" tip="$5" mode="$6" head="$7" db="$8"
  if (( dry )); then
    row removed "$path" "$branch" "" "$tip"; return 0
  fi
  if ! git -C "$shared" worktree remove "$path" 2>"$ERRFILE"; then
    row failed "$path" "$branch" "git worktree remove failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  # The removal happened: report it whatever the deletion does, so the JSON
  # matches the disk a retry would find (#405).
  row removed "$path" "$branch" "" "$tip"
  [[ -n "$branch" ]] || return 0
  local rc=0
  proof_holds "$shared" "$mode" "$tip" "$head" "$db" || rc=$?
  case "$rc" in
    0) ;;
    1) row failed "$branch" "$branch" "origin stopped holding ${tip} after its worktree was removed, so ${branch} was kept; push it or delete it by hand"; return 0 ;;
    *) row failed "$branch" "$branch" "cannot re-verify that origin holds ${tip} after its worktree was removed, so ${branch} was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
  esac
  delete_branch "$shared" "$branch" "$tip" || rc=$?
  report_branch_delete "$branch" "$tip" "$rc"
  return 0
}

# Echo whole hours since the newest activity on <branch>: its tip commit's
# committer time or its newest reflog entry, whichever is later. PRUNE_NOW
# (epoch seconds) replaces the clock for tests. Returns 1 on failure.
branch_idle_hours() { # <shared> <branch> <tip>
  local committed logged
  committed="$(git -C "$1" log -1 --format=%ct "$3" 2>"$ERRFILE")" || return 1
  # A branch without a reflog prints nothing and exits 0; any failure is real.
  logged="$(git -C "$1" reflog show -1 --format=%ct "refs/heads/$2" 2>"$ERRFILE")" || return 1
  python3 -c '
import sys, time
now = float(sys.argv[1]) if sys.argv[1] else time.time()
newest = max(int(v) for v in sys.argv[2:] if v)
print(int(max(0.0, now - newest) // 3600))' "${PRUNE_NOW:-}" "$committed" "$logged" 2>"$ERRFILE"
}

decide_branch() { # <shared> <default> <dry-run 0|1> <branch>
  local shared="$1" db="$2" dry="$3" branch="$4"
  if [[ "$branch" == "$db" ]]; then
    return 0
  fi
  local tip merged rc=0
  if ! tip="$(branch_tip "$shared" "$branch")"; then
    row failed "$branch" "$branch" "cannot read the tip of ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if ! merged="$(ancestry "$shared" "$tip" "$db")"; then
    row failed "$branch" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  # Merged into origin's default, or its tip held by an origin branch: the
  # branch holds nothing origin lacks.
  local mode=""
  [[ "$merged" == merged ]] && mode=merged
  if [[ -z "$mode" ]]; then
    rc=0; reachable_remotely "$shared" "$tip" || rc=$?
    case "$rc" in
      0) mode=reachable ;;
      1) ;;
      *) row failed "$branch" "$branch" "cannot read which origin branches hold ${tip}: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
    esac
  fi
  if [[ -z "$mode" ]]; then
    # Unpushed commits: kept, and reported once idle, never deleted.
    local age ahead
    if ! age="$(branch_idle_hours "$shared" "$branch" "$tip")"; then
      row failed "$branch" "$branch" "cannot read the age of ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
    fi
    if (( age < IDLE_HOURS )); then
      row branch-kept "$branch" "$branch" not-idle; return 0
    fi
    if ! ahead="$(unpushed_count "$shared" "$tip")"; then
      row failed "$branch" "$branch" "cannot count the unpushed commits of ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
    fi
    row branch-kept "$branch" "$branch" unpushed "$ahead" "$age"; return 0
  fi
  # Origin as it is now, the same proof a worktree removal takes, for the
  # preview as for the deletion.
  rc=0; proof_holds "$shared" "$mode" "$tip" "$tip" "$db" || rc=$?
  case "$rc" in
    0) ;;
    1) row branch-kept "$branch" "$branch" changed; warn "kept ${branch}: origin no longer holds ${tip}"; return 0 ;;
    *) row failed "$branch" "$branch" "cannot re-verify that origin holds ${tip}, so ${branch} was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
  esac
  if (( dry )); then
    row branch-deleted "$branch" "$branch" ""; return 0
  fi
  delete_branch "$shared" "$branch" "$tip" || rc=$?
  case "$rc" in
    0) row branch-deleted "$branch" "$branch" "" ;;
    1) row failed "$branch" "$branch" "branch ${branch} moved after its ancestry check and was left alone; re-run to judge the new tip" ;;
    3) row branch-deleted "$branch" "$branch" ""
       row failed "$branch" "$branch" "${branch} is deleted but its branch.${branch} config could not be cleaned up: $(tr '\n' ' ' < "$ERRFILE") — check and remove it by hand" ;;
    4) row failed "$branch" "$branch" "could not re-read which worktrees hold ${branch}, so it was left alone: $(tr '\n' ' ' < "$ERRFILE")" ;;
    5) row branch-kept "$branch" "$branch" checked-out ;;
    6) row failed "$branch" "$branch" "a worktree claimed ${branch} while it was being deleted; the branch was restored at ${tip} and that worktree is intact — re-run once it is gone" ;;
    7) row failed "$branch" "$branch" "a worktree claimed ${branch} while it was being deleted and it could not be restored: $(tr '\n' ' ' < "$ERRFILE") — inspect that worktree by hand" ;;
    8) row failed "$branch" "$branch" "${branch} is deleted and whether a worktree claimed it meanwhile could not be read: $(tr '\n' ' ' < "$ERRFILE") — check \`git worktree list\` and restore ${branch} at ${tip} if one holds it" ;;
    9) row branch-deleted "$branch" "$branch" ""
       row failed "$branch" "$branch" "${branch} is deleted, and a worktree recreated a branch of that name before its config could be cleaned up — branch.${branch} belongs to that live branch and was left untouched; remove nothing by hand" ;;
    *) row failed "$branch" "$branch" "deleting ${branch} failed: $(tr '\n' ' ' < "$ERRFILE")" ;;
  esac
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
    if ! command -v "$tool" >/dev/null; then
      warn "${tool} not found on PATH — install it"
      return 1
    fi
  done
  if ! WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/prune-worktrees.XXXXXX")"; then
    WORKDIR=""
    warn "cannot create a temporary directory under ${TMPDIR:-/tmp} — make it writable, then re-run"
    return 1
  fi
  trap cleanup EXIT
  ERRFILE="${WORKDIR}/err"; ROWS="${WORKDIR}/rows"
  : > "$ERRFILE"; : > "$ROWS"
  if [[ ! -d "$shared" ]] || ! git -C "$shared" rev-parse --is-inside-work-tree >/dev/null 2>"$ERRFILE"; then
    warn "'${shared}' is not a git work tree ($(tr '\n' ' ' < "$ERRFILE")) — pass the shared checkout's path"
    return 1
  fi
  local abs_shared
  # A sentinel past git's own newline: command substitution strips both, and a
  # path ending in a newline would lose its own.
  abs_shared="$(git -C "$shared" rev-parse --show-toplevel && printf x)"
  abs_shared="${abs_shared%x}"; abs_shared="${abs_shared%$'\n'}"
  if ! git -C "$shared" remote get-url origin >/dev/null 2>"$ERRFILE"; then
    warn "${shared} has no origin remote — merged-ness is judged against origin's default branch; add one with \`git -C ${shared} remote add origin <url>\`, then re-run"
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
  local frc=0
  git -C "$shared" "${fetch_args[@]}" 2>"$ERRFILE" || frc=$?
  if (( frc != 0 )); then
    network_failure "$frc" "$shared" "${fetch_args[@]}"
    warn "$(cat "$ERRFILE") — check connectivity; refusing to judge merged-ness from stale refs"
    return 1
  fi
  local src=0
  if (( ! dry )); then git -C "$shared" remote set-head origin --auto >/dev/null 2>"$ERRFILE" || src=$?; fi
  if (( src != 0 )); then
    network_failure "$src" "$shared" remote set-head origin --auto
    warn "$(cat "$ERRFILE") — cannot confirm origin's current default branch; fix access to origin, then re-run"
    return 1
  fi
  if ! read_origin_tips "$shared"; then
    warn "cannot read origin's current branch tips: $(tr '\n' ' ' < "$ERRFILE") — refusing to judge reachability from stale refs; fix access to origin, then re-run"
    return 1
  fi
  local db
  if ! db="$(default_branch_of "$shared" "$dry")"; then
    warn "cannot resolve origin's default branch — run \`git -C ${shared} remote set-head origin --auto\`"
    return 1
  fi
  if ! ORIGIN_DEFAULT="$(origin_default_tip "$shared" "$db")"; then
    warn "cannot read origin's current ${db}: $(cat "$ERRFILE") — refusing to judge merged-ness; fix access to origin, then re-run"
    return 1
  fi

  # Take both inventories up front: a failure inside a process substitution
  # would not reach the loop, and an empty inventory would read as "nothing
  # to prune" with exit 0 (rules/file-hygiene.md I/O Conventions). Nothing has
  # been decided yet, so an unreadable inventory is a precondition failure.
  local inventory branches
  inventory="${WORKDIR}/inventory"; branches="${WORKDIR}/branches"
  # `-z` (git >= 2.36) terminates each attribute with NUL, so a path holding a
  # newline stays one field. Without it there is no unambiguous read and no
  # cross-check that settles one: a scan refusing unrecognized lines still
  # accepts a path whose tail reads as an attribute (`...<newline>HEAD <sha>`,
  # `...<newline>branch refs/heads/other`), and the parser then takes that
  # tail for the record's metadata. So an inventory without `-z` decides
  # nothing (#410).
  if ! git -C "$shared" worktree list --porcelain -z >"$inventory" 2>"$ERRFILE"; then
    if grep -qiE 'unknown option|usage: git worktree' "$ERRFILE"; then
      warn "\`git worktree list --porcelain -z\` is unavailable (git < 2.36): a worktree path cannot be listed unambiguously — upgrade git to 2.36 or newer; nothing was decided"
    else
      warn "\`git worktree list --porcelain -z\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory worktrees; run it in ${shared} to see why, then re-run"
    fi
    rm -f "$inventory" "$branches"
    return 1
  fi
  # Full refnames: `%(refname:short)` renders a local branch named like a
  # remote-tracking ref (origin/main) as heads/origin/main.
  if ! git -C "$shared" for-each-ref --format='%(refname)' refs/heads/ >"$branches" 2>"$ERRFILE"; then
    warn "\`git for-each-ref refs/heads/\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory branches; run it in ${shared} to see why, then re-run"
    rm -f "$inventory" "$branches"
    return 1
  fi

  # Walk `worktree list --porcelain`: blank-line-separated blocks.
  local path="" branch="" detached=0 locked=0 lock_reason="" line unenterable=0
  local -a seen_branches=() prunable_branches=()
  flush() {
    if [[ -n "$path" ]]; then
      local real="" rc=0 parent
      # The sentinel guards the same truncation `pwd -P` needs it for below:
      # when the PARENT's own name ends in a newline, `dirname`'s output ends
      # in two and command substitution strips both (#410).
      parent="$(dirname "$path" && printf 'x')"
      parent="${parent%x}"
      parent="${parent%$'\n'}"
      if [[ ! -e "$path" ]]; then
        # `-e` is false for a missing path and for one whose ancestor denies
        # traversal. Absence is confirmed only through a traversable parent;
        # anything else is a failure that also inhibits the metadata prune.
        if [[ -d "$parent" && -x "$parent" ]]; then
          # Confirmed gone: reported prunable. Its branch goes to the
          # no-worktree pass only once the metadata prune actually releases
          # it, so it is recorded here and released below.
          decide_worktree "$shared" "$abs_root" "$db" "$dry" "$path" "$branch" "$detached" "$locked" "$lock_reason"
          if [[ -n "$branch" ]]; then
            if (( DECIDED_PRUNABLE )); then
              prunable_branches+=("$branch")
            else
              # Kept for a reason that outranks prunable (locked, detached,
              # outside the root, the default branch): still checked out.
              seen_branches+=("$branch")
            fi
          fi
          path=""; branch=""; detached=0; locked=0; lock_reason=""
          return 0
        else
          row failed "$path" "$branch" "cannot confirm the worktree is gone: its parent ${parent} is missing or not traversable"
          # Still checked out as far as git knows: the branch pass must not
          # try `branch -D` on it (#405).
          if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
          unenterable=1
          path=""; branch=""; detached=0; locked=0; lock_reason=""
          return 0
        fi
      else
        # A sentinel past `pwd`'s own newline: command substitution strips
        # trailing newlines, and a worktree path may end in one now that `-z`
        # can carry it. Without this the run would decide against a truncated
        # path.
        real="$(cd "$path" 2>"$ERRFILE" && pwd -P && printf 'x')" || rc=$?
        real="${real%x}"
        real="${real%$'\n'}"
        if (( rc != 0 )); then
          row failed "$path" "$branch" "cannot enter the worktree: $(tr '\n' ' ' < "$ERRFILE")"
          if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
          unenterable=1
          path=""; branch=""; detached=0; locked=0; lock_reason=""
          return 0
        fi
      fi
      if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
      if [[ "$real" != "$abs_shared" ]]; then
        decide_worktree "$shared" "$abs_root" "$db" "$dry" "$real" "$branch" "$detached" "$locked" "$lock_reason"
      fi
    fi
    path=""; branch=""; detached=0; locked=0; lock_reason=""
  }
  while IFS= read -r -d '' line || [[ -n "$line" ]]; do
    case "$line" in
      "worktree "*) flush; path="${line#worktree }" ;;
      "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
      detached) detached=1 ;;
      locked) locked=1 ;;
      "locked "*) locked=1; lock_reason="${line#locked }" ;;
      "") flush ;;
    esac
  done < "$inventory"
  flush

  # Metadata prune between the passes: after every worktree decision, so git reads an unreadable worktree directory as gone
  # a confirmed-gone entry is released before its branch is judged below;
  # skipped when a worktree could not be entered, since git reads an unreadable
  # directory as gone and would drop its entry.
  # A prunable entry's branch is still checked out until its metadata goes,
  # so it is released to the branch pass only when the prune ran clean. A dry
  # run previews the live outcome, where the prune does run (#405).
  local released=1
  # An unenterable worktree stops the prune in a live run, so a dry run defers
  # the same branches: a preview that promises a deletion the live run would
  # not make is worse than no preview.
  if (( unenterable )); then released=0; fi
  if (( ! dry )); then
    if (( unenterable )); then
      warn "skipping \`git worktree prune\`: a worktree could not be entered; restore access and re-run"
    elif ! git -C "$shared" worktree prune --expire now 2>"$ERRFILE"; then
      # Recorded, not merely warned: the run continues, the exit stays non-zero.
      row failed "git worktree prune" "" "failed: $(tr '\n' ' ' < "$ERRFILE") — stale metadata may remain"
      released=0
    fi
  fi
  if (( ! released )); then
    seen_branches+=("${prunable_branches[@]+"${prunable_branches[@]}"}")
  fi

  # Local branches with no worktree.
  local name skip
  while IFS= read -r name; do
    name="${name#refs/heads/}"
    skip=0
    local s
    for s in "${seen_branches[@]+"${seen_branches[@]}"}"; do
      if [[ "$s" == "$name" ]]; then skip=1; break; fi
    done
    if (( skip )); then continue; fi
    decide_branch "$shared" "$db" "$dry" "$name"
  done < "$branches"
  if ! rm -f "$inventory" "$branches"; then
    warn "could not remove temp inventories ${inventory} ${branches} — remove them by hand"
  fi

  local rc=0
  python3 - "$abs_shared" "$db" "$dry" "$ROWS" <<'PY' || rc=$?
import json, shlex, sys
shared, db, dry, rows_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
result = {"shared": shared, "default_branch": db, "dry_run": dry, "worktrees_removed": [], "worktrees_kept": [],
          "branches_deleted": [], "branches_kept": [], "failed": []}
with open(rows_path, "rb") as handle:
    fields = handle.read().decode("utf-8", "surrogateescape").split("\0")
if fields and fields[-1] == "":
    fields.pop()
if len(fields) % 6:
    sys.stderr.write("prune-worktrees: decision rows are malformed; report this as a bug\n")
    sys.exit(2)
for index in range(0, len(fields), 6):
    kind, target, branch, reason, head, extra = fields[index:index + 6]
    if kind == "removed":
        result["worktrees_removed"].append({"path": target, "branch": branch or None, "head": head})
    elif kind == "kept":
        kept = {"path": target, "branch": branch or None, "reason": reason}
        if reason == "locked":
            kept["lock_reason"] = extra or None
        if reason in ("dirty", "unpushed"):
            # Work git or origin does not hold: the operator decides.
            where = shlex.quote(target)
            count, age = (int(v) for v in extra.split(" "))
            kept["head"] = head
            kept["age_hours"] = age
            if reason == "dirty":
                kept["dirty_files"] = count
                kept["command"] = "git -C {} status".format(where)
            else:
                kept["unpushed_commits"] = count
                kept["command"] = ("git -C {} push -u origin HEAD".format(where) if branch else
                                   "git -C {0} switch -c <branch> && git -C {0} push -u origin HEAD".format(where))
        result["worktrees_kept"].append(kept)
    elif kind == "branch-deleted":
        result["branches_deleted"].append(branch)
    elif kind == "branch-kept":
        entry = {"branch": branch, "reason": reason}
        if reason == "unpushed":
            entry.update(unpushed_commits=int(head), age_hours=int(extra),
                         command="git -C {} push -u origin {}".format(shlex.quote(shared), shlex.quote(branch)))
        result["branches_kept"].append(entry)
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
