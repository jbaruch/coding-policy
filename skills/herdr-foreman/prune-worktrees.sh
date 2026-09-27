#!/usr/bin/env bash
# Remove or archive the worker worktrees and local branches a round left behind.
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
#   * REMOVED (its branch deleted) when clean, IDLE for IDLE_HOURS, and its
#     branch is an ancestor of the commit origin's default branch points at
#     right now (`git ls-remote`, ORIGIN_DEFAULT; re-read before the removal);
#   * REMOVED when clean, IDLE for IDLE_HOURS, and its HEAD is an ancestor of
#     a branch tip origin holds right now (detached included): the tips come
#     from `git ls-remote --heads origin` (ORIGIN_TIPS), never from possibly
#     stale local remote-tracking refs, so a dry run and a live run judge the
#     same origin; its branch, if any, is deleted at that tip;
#   * ARCHIVED then MOVED TO TRASH when dirty or holding commits origin lacks,
#     and IDLE for ARCHIVE_IDLE_HOURS: its HEAD plus every tracked and
#     untracked non-ignored file become one commit at
#     refs/archive/worktrees/<name>-<pathhash>-<UTC stamp>, whose JSON note
#     under ARCHIVE_NOTES is the archive record (state-schema.md Worktree
#     Archives); the commit message names the ref and source, so each archive
#     is its own commit; the note is written before the ref and never over an
#     existing note; then `git worktree move`
#     renames it to <root>/.trash/<same name> and locks it with the reason
#     `prune-worktrees archive <ref>`. Nothing on this path deletes: a writer
#     that got in after the last check lands in the moved copy. A failed move
#     leaves it in place with the archive resolved (reported), and while an
#     archive of this path waits for its trash, no other is written
#     (archive-pending).
# Before either removal or archive, a worktree holding another repository's
# checkout is KEPT: a gitlink found from the index (never .gitmodules) whose
# checkout changed (submodule-dirty) or is populated (submodule), or an
# untracked directory with its own .git (nested-repo).
# A git command that talks to origin (fetch, ls-remote, set-head) that fails
# is reported by exit code and the command to rerun, never by its own message,
# which can carry the remote URL with credentials (`network_failure`).
# The removals above are plain `git worktree remove`, never forced: git
# refuses a tree that turned dirty, which is that path's atomic guard. The
# removal proof (merged, or origin holds HEAD) is re-derived immediately
# before the removal, after the recheck below; a proof gone keeps it.
# A trash worktree is never judged again; the expiry pass below owns it.
# "Clean" is `git status --porcelain --untracked-files=all` empty — untracked
# files count as dirty whatever `status.showUntrackedFiles` says; ignored files
# do not, they are reproducible by the ignore's own claim.
# "IDLE for N hours" is both: no process of this user has its cwd inside the
# worktree (`lsof -F pn0`; a missing or failing probe keeps every worktree,
# reason idle-unknown, and so does a path lsof cannot print faithfully — one
# holding a control byte, a backslash or a non-ASCII byte, which lsof escapes),
# and the newest mtime among the worktree directory, its own
# gitdir's HEAD, index and logs/HEAD, and every modified tracked or untracked
# non-ignored file is at least N hours old (a worktree holding more such files
# than ACTIVITY_FILE_LIMIT is never idle). Every read runs with
# --no-optional-locks, so judging never rewrites the index. Immediately
# before each removal, and again after an archive is written, HEAD, the branch
# tip, the status, the age and the process probe are all read again; any
# change keeps the worktree (reason changed). IDLE_HOURS, ARCHIVE_IDLE_HOURS,
# ACTIVITY_FILE_LIMIT, ARCHIVE_EXPIRE_DAYS, ORPHAN_NOTE_GRACE_HOURS,
# ARCHIVE_SCHEMA and ARCHIVE_NOTES are constants beside the functions that use
# them.
# Expiry, each live run, for every archive record older than
# ARCHIVE_EXPIRE_DAYS (`plan_archives`, `trash_gates`, `expire_archives`):
#   * the record is read from its note; an older schema_version is migrated
#     through MIGRATIONS and rewritten; a newer, missing, unparseable or
#     unmigratable record is KEPT and reported, never expired;
#   * the record must name this ref and its stamp, hash its source to the
#     ref's path hash, name the archive commit's parent as head and its tree
#     as tree, and name exactly <root>/.trash/<ref basename> as trash — a
#     record that fails any of these is KEPT (record-invalid);
#   * the trash worktree must exist (a missing one KEEPS the archive), resolve
#     to that exact path, be registered here, carry the sweep's own lock and
#     no other, be on the recorded HEAD and branch, be IDLE for
#     ARCHIVE_IDLE_HOURS with no process inside, and snapshot to exactly the
#     recorded tree; anything else KEEPS the archive;
#   * the branch is read with absence told apart from a git error (an error
#     KEEPS the archive); only a tip equal to the recorded head is deleted;
#   * then, in order: the process probe is re-read fresh, the fingerprint
#     recomputed, and the registration, HEAD, branch and lock re-read (any
#     change keeps it); the trash worktree is force-removed
#     past its lock (safe: its content is the archive); the branch is
#     deleted; the ref is compare-and-deleted; its note is removed last, so
#     a ref never exists without its record. A failure before the ref
#     deletion keeps the ref and its record; a failure after it leaves an
#     orphan note on an unreferenced commit, which no reader takes for an
#     archive.
# Orphan notes (`remove_orphan_notes`): a note under ARCHIVE_NOTES on a commit
# no archive ref points at, older than ORPHAN_NOTE_GRACE_HOURS (so an archive
# whose note is written but whose ref is not yet is never taken for one), is
# removed each live run and listed under `orphan_notes_removed`.
# A trash worktree is never pruned or released as an ordinary worktree, even
# when its directory is missing: expiry reports it and its branch stays held.
# A ref not named <name>-<10 hex>-<UTC stamp>, with <name> drawn only from
# the A-Za-z0-9._- charset archive_names generates, is never touched.
# Every judgment reads origin's refs fetched by THIS run and origin's default branch as
# re-queried by THIS run — a fetch or default-branch lookup that fails is a
# precondition failure, never a judgment from stale refs. Every removal is
# restorable: `git worktree add <path> <head>` or `<archive_ref>`.
# A local branch with no worktree is DELETED iff it is not the default branch
# and is an ancestor of origin's default branch. That ancestry check is the
# safety, and it judges a captured commit rather than a name that can move.
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
# never `rm -rf`. Everything else is KEPT and reported with its reason. Stale
# worktree metadata is pruned (`git worktree prune --expire now`) after every
# worktree decision and before the branch pass, so a confirmed-gone entry's
# merged branch goes in the same run; the prune is skipped when any worktree
# could not be entered, since git would read an unreadable directory as gone
# and drop its entry. Nothing here
# pushes to origin; a dry run still fetches (without --prune), reads origin's
# default branch with `ls-remote --symref` instead of rewriting origin/HEAD,
# and skips the metadata prune, so its decisions are current and .git is
# otherwise untouched.
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
#            "worktrees_archived":[{"path","branch","head","archive_ref","trash_path"}],
#            "archives_expired":[{"ref","head","trash_path","branch"}],
#            "archives_kept":[{"ref","head","trash_path","reason"}],
#            "archives_migrated":[{"ref","head"}],
#            "orphan_notes_removed":["<commit>"],
#            "worktrees_kept":[{"path","branch","reason"[,"lock_reason"]}],
#            "branches_deleted":["<name>"],
#            "branches_kept":[{"branch","reason"}],
#            "failed":[{"target","error"}]}
#           reason is one of: checked-out (a worktree claimed the branch after
#           the inventory was taken), default-branch, detached, dirty,
#           changed (it changed between judgment and removal), idle-unknown
#           (the process probe could not run, or cannot print this path
#           faithfully), in-use (a process works
#           inside it), locked (with its lock_reason), merged-not-idle,
#           archive-pending (an earlier archive of this path waits for its
#           trash; carries archive_ref), trash-unsafe (the root's .trash is a
#           symlink or not a directory; nothing archived), nested-repo, outside-root, submodule,
#           submodule-dirty,
#           prunable (its directory is gone; a live run's metadata prune
#           removes it), unmerged. detached, dirty and unmerged mean not idle
#           long enough for the matching removal. An archived entry whose
#           trash_path is null kept its worktree in place (a change after the
#           snapshot, or a failed move, also reported). A dry run reports the
#           archive_ref it would write and the archives it would expire.
#   stderr: diagnostics only.
#   exit  : 0 every decision applied (or previewed),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             no origin, fetch failed, default branch unresolvable, worktree
#             or branch inventory unreadable) — no JSON, nothing decided,
#           2 at least one check, removal, archive, move, expiry step or
#             deletion failed; the rest still ran and `failed` names each one.
#   env   : WORKTREE_ROOT overrides the worktree root (default
#           $HOME/.worktrees); the tests point it at a temp dir.
#           PRUNE_IDLE_HOURS / PRUNE_ARCHIVE_IDLE_HOURS /
#           PRUNE_ARCHIVE_EXPIRE_DAYS / PRUNE_ACTIVITY_FILE_LIMIT override the
#           constants, PRUNE_NOW (epoch seconds) the clock, PRUNE_LSOF the probe.
set -euo pipefail

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
  for f in "$ERRFILE" "$ROWS" "$CWD_FILE" "$ORIGIN_TIPS"; do
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
    *) warn "\`git show-ref --verify ${2}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"; return 2 ;;
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
      *) warn "\`git symbolic-ref refs/remotes/origin/HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"; return 1 ;;
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

# Report a branch deletion that followed a worktree removal or archive.
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
    if ! command -v "$lsof_bin" >/dev/null 2>&1; then
      warn "${lsof_bin} not found on PATH — cannot tell whether a worktree is in use, so none is judged idle; install lsof"
    elif ! uid="$(id -u)"; then
      warn "id -u failed — cannot scope the process probe, so no worktree is judged idle"
    elif [[ -z "$CWD_FILE" ]] && ! CWD_FILE="$(mktemp)"; then
      CWD_FILE=""
      warn "mktemp failed — cannot hold the process probe, so no worktree is judged idle"
    elif ! "$lsof_bin" -a -u "$uid" -d cwd -F pn0 >"$CWD_FILE" 2>"$ERRFILE"; then
      warn "\`${lsof_bin} -a -u ${uid} -d cwd -F pn0\` failed: $(tr '\n' ' ' < "$ERRFILE") — no worktree is judged idle"
    else
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
  local now_state age rc=0
  RECHECK_WHY=""
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
with open(listing, encoding="utf-8") as handle:
    shas = sorted({line.split("\t", 1)[0] for line in handle if line.strip()})
check = subprocess.run(["git", "-C", shared, "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                       input="".join(sha + "\n" for sha in shas), capture_output=True, text=True)
if check.returncode != 0:
    sys.stderr.write(check.stderr)
    sys.exit(1)
present = [line.split(" ")[0] for line in check.stdout.splitlines() if line.endswith(" commit")]
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
proof_holds() { # <shared> <mode> <tip> <head> <default>
  if [[ "$2" == merged ]]; then
    # Origin's default as it is now: a force-push since the run started can
    # drop the merge.
    local now_tip trc=0
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

#: The identity every commit this script writes carries: the archive commit
#: and each notes-ref commit (`notes add`, `notes remove`). Never the
#: operator's config, which a runner or a fresh machine may not have.
GIT_IDENT=(GIT_AUTHOR_NAME=prune-worktrees GIT_AUTHOR_EMAIL=prune-worktrees@localhost
           GIT_COMMITTER_NAME=prune-worktrees GIT_COMMITTER_EMAIL=prune-worktrees@localhost)
# An injected clock dates the commits too, so their ages read against it.
if [[ -n "${PRUNE_NOW:-}" ]]; then
  GIT_IDENT+=("GIT_AUTHOR_DATE=@${PRUNE_NOW%.*} +0000" "GIT_COMMITTER_DATE=@${PRUNE_NOW%.*} +0000")
fi

#: The archive record's schema_version, written into its JSON note. The
#: owner is this script; MIGRATIONS in `plan_archives` upgrades an older record.
ARCHIVE_SCHEMA=1
#: The notes ref holding one JSON archive record per archive commit.
ARCHIVE_NOTES=refs/notes/worktree-archive

# Echo "<ref> <trash-path>" for archiving the worktree at <real> now: the ref
# refs/archive/worktrees/<name>-<pathhash>-<UTC stamp> and the trash path
# <root>/.trash/<name>-<pathhash>-<UTC stamp>. The path hash keeps two
# worktrees with one basename apart. Returns 1 on failure.
archive_names() { # <abs_root> <real>
  local stamp hash name
  if ! stamp="$(python3 -c '
import datetime, sys, time
now = float(sys.argv[1]) if sys.argv[1] else time.time()
print(datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))' "${PRUNE_NOW:-}" 2>"$ERRFILE")"; then
    return 1
  fi
  if ! hash="$(python3 -c 'import hashlib, sys; print(hashlib.sha1(sys.argv[1].encode("utf-8", "surrogateescape")).hexdigest()[:10])' "$2" 2>"$ERRFILE")"; then
    return 1
  fi
  name="${2%/}"; name="${name##*/}"
  name="$(printf '%s' "$name" | LC_ALL=C tr -c 'A-Za-z0-9._-' '-')"
  name="${name#.}"; name="${name:-worktree}"
  if ! git check-ref-format "refs/archive/worktrees/${name}-${hash}-${stamp}" 2>"$ERRFILE"; then
    printf 'archive ref for %s is not a valid ref name\n' "$2" > "$ERRFILE"
    return 1
  fi
  printf '%s %s' "refs/archive/worktrees/${name}-${hash}-${stamp}" "$1/.trash/${name}-${hash}-${stamp}"
}

# Echo the tree id of the worktree at <real> as it stands — its HEAD plus every
# tracked and untracked non-ignored file — through a temporary index, never
# the worktree's own. This is the archive's fingerprint.
snapshot_tree() { # <real> <head>
  local scratch tree="" ok=0
  if ! scratch="$(mktemp -d 2>"$ERRFILE")"; then return 1; fi
  if GIT_INDEX_FILE="${scratch}/index" git -C "$1" read-tree "$2" 2>"$ERRFILE" \
    && GIT_INDEX_FILE="${scratch}/index" git -C "$1" add -A 2>"$ERRFILE" \
    && tree="$(GIT_INDEX_FILE="${scratch}/index" git -C "$1" write-tree 2>"$ERRFILE")"; then
    ok=1
  fi
  if ! rm -rf "$scratch"; then warn "could not remove temp dir ${scratch} — remove it by hand"; fi
  (( ok )) || return 1
  printf '%s' "$tree"
}

# Snapshot the worktree at <real> into a new commit at <ref> whose JSON note
# under ARCHIVE_NOTES is the archive record. The note is written before the
# ref, so no archive ref exists without its record, and never with -f: a note
# already on that commit refuses the archive rather than overwrite another
# archive's record. Returns 1 on any failure,
# leaving ERRFILE with the reason and the worktree untouched.
archive_worktree() { # <shared> <real> <head> <branch|""> <ref> <trash-path> <dry 0|1>
  local shared="$1" real="$2" head="$3" branch="$4" ref="$5" trash="$6" dry="$7" tree commit resolved note
  (( dry )) && return 0
  tree="$(snapshot_tree "$real" "$head")" || return 1
  # The message names the ref and the source path, so two archives of one
  # parent and tree in one second are still two commits, each with its own
  # note.
  commit="$(env "${GIT_IDENT[@]}" git -C "$real" commit-tree "$tree" -p "$head" \
    -m "Archive ${ref}" -m "Source: ${real}" 2>"$ERRFILE")" || return 1
  if ! note="$(mktemp 2>"$ERRFILE")"; then return 1; fi
  local ok=0
  if python3 - "$note" "$ARCHIVE_SCHEMA" "$ref" "$real" "$trash" "$head" "$branch" "$tree" 2>"$ERRFILE" <<'PY' \
    && env "${GIT_IDENT[@]}" git -C "$shared" notes --ref="$ARCHIVE_NOTES" add -F "$note" "$commit" 2>"$ERRFILE" \
    && git -C "$shared" update-ref "$ref" "$commit" "" 2>"$ERRFILE" \
    && resolved="$(git -C "$shared" rev-parse --verify --quiet "${ref}^{commit}" 2>"$ERRFILE")" \
    && [[ "$resolved" == "$commit" ]]; then
import json
import re
import sys

out, schema, ref, source, trash, head, branch, tree = sys.argv[1:9]
stamp = re.search(r"-(\d{8}T\d{6}Z)$", ref).group(1)
with open(out, "w", encoding="utf-8", errors="surrogateescape") as handle:
    json.dump({"schema_version": int(schema), "ref": ref, "source": source, "trash": trash, "head": head,
               "branch": branch or None, "stamp": stamp, "tree": tree}, handle, sort_keys=True)
PY
    ok=1
  fi
  if ! rm -f "$note"; then warn "could not remove temp file ${note} — remove it by hand"; fi
  if (( ! ok )); then
    [[ -s "$ERRFILE" ]] || printf 'archive ref %s did not resolve to the snapshot commit\n' "$ref" > "$ERRFILE"
    return 1
  fi
}

# Echo a keep reason when the worktree at <real> holds another repository's
# checkout that neither the archive nor a removal can carry, else nothing:
#   submodule-dirty  a gitlink (mode 160000, found from the index, never from
#                    .gitmodules) whose checkout has a new commit, changes or
#                    untracked files
#   submodule        a populated gitlink checkout; git refuses to move or
#                    remove a worktree holding one
#   nested-repo      any other .git anywhere below the worktree, in untracked
#                    or ignored directories alike, found by walking the tree;
#                    `add -A` would record one as a bare gitlink or skip it
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
for current, dirs, files in os.walk(path, followlinks=False):
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

#: Idle windows (hours since the newest activity), overridable for tests:
#: a clean worktree whose HEAD a remote ref holds is removed after IDLE_HOURS;
#: a dirty or unpushed one is archived under refs/archive/worktrees/ and moved
#: to the root's .trash/ after ARCHIVE_IDLE_HOURS.
IDLE_HOURS="${PRUNE_IDLE_HOURS:-24}"
ARCHIVE_IDLE_HOURS="${PRUNE_ARCHIVE_IDLE_HOURS:-72}"

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
  local merged=""
  if [[ -n "$branch" ]] && ! merged="$(ancestry "$shared" "$tip" "$db")"; then
    row failed "$path" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  local legacy=unmerged
  [[ -n "$status" ]] && legacy=dirty
  [[ -z "$status" && -z "$branch" ]] && legacy=detached
  [[ -z "$status" && "$merged" == merged ]] && legacy="merged-not-idle"
  if (( ! age_ok )) || (( age < IDLE_HOURS )); then
    row kept "$path" "$branch" "$legacy"; return 0
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
  local reach=0
  if [[ "$merged" != merged ]]; then
    rc=0; reachable_remotely "$shared" "$head" || rc=$?
    case "$rc" in
      0) reach=1 ;;
      1) ;;
      *) row failed "$path" "$branch" "cannot read which remote refs hold ${head}: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
    esac
  fi
  if [[ -z "$status" ]] && [[ "$merged" == merged ]] || { [[ -z "$status" ]] && (( reach )); }; then
    local mode=reachable
    [[ "$merged" == merged ]] && mode=merged
    if (( ! dry )) && ! recheck "$shared" "$path" "$branch" "$state" "$IDLE_HOURS"; then
      row kept "$path" "$branch" changed; warn "kept ${path}: ${RECHECK_WHY}"; return 0
    fi
    # The proof is remote state a concurrent fetch can change: re-derive it last.
    rc=0; proof_holds "$shared" "$mode" "$tip" "$head" "$db" || rc=$?
    case "$rc" in
      0) ;;
      1) row kept "$path" "$branch" changed; warn "kept ${path}: origin no longer holds ${head}"; return 0 ;;
      *) row failed "$path" "$branch" "cannot re-verify that origin holds ${head}, so it was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0 ;;
    esac
    remove_worktree "$shared" "$dry" "$path" "$branch" "$tip" removed ""; return 0
  fi
  if (( age < ARCHIVE_IDLE_HOURS )); then
    row kept "$path" "$branch" "$legacy"; return 0
  fi
  # The trash must be a real directory under the root before anything is
  # written: a symlinked or non-directory .trash would carry the move
  # elsewhere while the record still claims the root.
  if [[ -L "$abs_root/.trash" || ( -e "$abs_root/.trash" && ! -d "$abs_root/.trash" ) ]]; then
    warn "kept ${path}: ${abs_root}/.trash is a symlink or not a directory — make it a plain directory so archives can be moved there"
    row kept "$path" "$branch" trash-unsafe; return 0
  fi
  local names ref trash
  if ! names="$(archive_names "$abs_root" "$path")"; then
    row failed "$path" "$branch" "cannot name the archive, so the worktree was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  ref="${names%% *}"; trash="${names#* }"
  # An earlier archive of this same path whose trash never came to exist (a
  # failed move) is still waiting on the operator: never stack another.
  local pending
  if ! pending="$(git -C "$shared" for-each-ref --format='%(refname)' "${ref%-*}-*" 2>"$ERRFILE")"; then
    row failed "$path" "$branch" "cannot list earlier archives of this path, so it was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  local earlier
  while IFS= read -r earlier; do
    [[ -n "$earlier" ]] || continue
    if [[ ! -e "$abs_root/.trash/${earlier#refs/archive/worktrees/}" ]]; then
      row kept "$path" "$branch" archive-pending "" "$earlier"; return 0
    fi
  done <<<"$pending"
  if ! archive_worktree "$shared" "$path" "$head" "$branch" "$ref" "$trash" "$dry"; then
    row failed "$path" "$branch" "archiving before removal failed, so the worktree was kept: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if (( dry )); then
    row archived "$path" "$branch" "" "$head" "${ref}"$'\t'"${trash}"; return 0
  fi
  # The snapshot took time: anything written since is not in it.
  if ! recheck "$shared" "$path" "$branch" "$state" "$ARCHIVE_IDLE_HOURS"; then
    row archived "$path" "$branch" "" "$head" "$ref"
    row kept "$path" "$branch" changed; warn "kept ${path} after archiving it to ${ref}: ${RECHECK_WHY}"; return 0
  fi
  # Never a delete: an atomic rename into the trash. A writer that got in after
  # the recheck lands in the moved copy, which the expiry pass removes with
  # the ref.
  # No -p: it would follow a symlink swapped in since the check above.
  if { [[ ! -d "$abs_root/.trash" ]] && ! mkdir "$abs_root/.trash" 2>"$ERRFILE"; } \
    || [[ -L "$abs_root/.trash" || ! -d "$abs_root/.trash" ]] \
    || ! git -C "$shared" worktree move "$path" "$trash" 2>"$ERRFILE"; then
    row archived "$path" "$branch" "" "$head" "$ref"
    row failed "$path" "$branch" "moving it to ${trash} failed, so it stayed in place: $(tr '\n' ' ' < "$ERRFILE") — its archive ${ref} was kept"; return 0
  fi
  row archived "$path" "$branch" "" "$head" "${ref}"$'\t'"${trash}"
  # The sweep's own lock: `git worktree prune` keeps a locked entry, so a trash
  # worktree whose directory vanishes stays registered for expiry to report.
  if ! git -C "$shared" worktree lock --reason "$(trash_lock_reason "$ref")" "$trash" 2>"$ERRFILE"; then
    row failed "$trash" "$branch" "locking the trash worktree failed: $(tr '\n' ' ' < "$ERRFILE") — expiry keeps ${ref} until it carries the sweep's lock"
  fi
  return 0
}

# Remove the worktree at <path>, report it as <kind> (removed|archived), then
# delete its branch at <tip>. A removed branch tip is held by origin's default
# or another remote ref; an archived one by its archive ref.
remove_worktree() { # <shared> <dry> <path> <branch> <tip> <kind> <archive-ref> [--force]
  local shared="$1" dry="$2" path="$3" branch="$4" tip="$5" kind="$6" ref="$7" force="${8:-}"
  local -a remove=(worktree remove)
  [[ -n "$force" ]] && remove+=(--force)
  if (( dry )); then
    row "$kind" "$path" "$branch" "" "$tip" "$ref"; return 0
  fi
  if ! git -C "$shared" "${remove[@]}" "$path" 2>"$ERRFILE"; then
    row failed "$path" "$branch" "git worktree remove failed: $(tr '\n' ' ' < "$ERRFILE")${ref:+ — its archive ${ref} was kept}"; return 0
  fi
  # The removal happened: report it whatever the deletion does, so the JSON
  # matches the disk a retry would find (#405).
  row "$kind" "$path" "$branch" "" "$tip" "$ref"
  [[ -n "$branch" ]] || return 0
  local rc=0
  delete_branch "$shared" "$branch" "$tip" || rc=$?
  report_branch_delete "$branch" "$tip" "$rc"
  return 0
}

#: Archive records older than this many days are expired by every live run.
ARCHIVE_EXPIRE_DAYS="${PRUNE_ARCHIVE_EXPIRE_DAYS:-30}"

# Read every archive record in <shared> and print one NUL-terminated plan line
# per ref: action, ref, sha, reason and record JSON, joined by \x1f (never a
# whitespace IFS character, so an empty field is kept). action is
# expire|unusable|migrated. It migrates an older record through MIGRATIONS and
# rewrites its note (live runs only); a record it cannot use is never expired.
# Every name, path and head check that needs no filesystem gate happens here.
plan_archives() { # <shared> <abs_root> <dry 0|1> <out-file>
  python3 - "$1" "$2" "$3" "$4" "${PRUNE_NOW:-}" "$ARCHIVE_EXPIRE_DAYS" "$ARCHIVE_SCHEMA" "$ARCHIVE_NOTES" 2>"$ERRFILE" <<'PY'
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

shared, root, dry, out, now, days, current, notes_ref = sys.argv[1:9]
dry, current = dry == "1", int(current)
now = float(now) if now else time.time()
cutoff = now - int(days) * 86400
IDENT = dict(os.environ, GIT_AUTHOR_NAME="prune-worktrees", GIT_AUTHOR_EMAIL="prune-worktrees@localhost",
             GIT_COMMITTER_NAME="prune-worktrees", GIT_COMMITTER_EMAIL="prune-worktrees@localhost")

# One entry per schema_version this owner can upgrade FROM: MIGRATIONS[n] takes
# a version-n record and returns the version n+1 record. Version 1 is the first
# schema, so the table is empty. A v2 bump adds MIGRATIONS[1] and raises
# ARCHIVE_SCHEMA in prune-worktrees.sh.
MIGRATIONS = {}

# Only the shape `archive_names` generates: the name part is its sanitized
# charset, so a ref with a slash in its basename is never this script's.
NAME = re.compile(r"^refs/archive/worktrees/(?P<base>(?P<name>[A-Za-z0-9._-]+)-(?P<hash>[0-9a-f]{10})-(?P<stamp>\d{8}T\d{6}Z))$")


def git(*args, env=None, stdin=None):
    return subprocess.run(["git", "-C", shared, *args], capture_output=True, env=env, input=stdin)


listed = git("for-each-ref", "--format=%(refname)%00%(objectname)", "refs/archive/worktrees/")
if listed.returncode != 0:
    sys.stderr.write(listed.stderr.decode("utf-8", "replace"))
    sys.exit(1)
plan = []
for line in listed.stdout.decode("utf-8", "surrogateescape").splitlines():
    ref, _, sha = line.partition("\0")

    def emit(action, reason, record=None):
        plan.append("\x1f".join([action, ref, sha, reason, json.dumps(record or {}, sort_keys=True)]))

    match = NAME.match(ref)
    if not match:
        continue  # not a name this script writes: never touched, not reported
    note = git("notes", "--ref=" + notes_ref, "show", sha)
    if note.returncode != 0:
        emit("unusable", "record-missing"); continue
    try:
        record = json.loads(note.stdout.decode("utf-8", "surrogateescape"))
    except ValueError:
        emit("unusable", "record-unparseable"); continue
    version = record.get("schema_version") if isinstance(record, dict) else None
    # type() rather than isinstance(): a JSON true is a bool, which Python
    # would otherwise accept as the integer 1.
    if type(version) is not int or version < 1:
        emit("unusable", "record-unparseable"); continue
    if version > current:
        emit("unusable", "schema-newer"); continue
    migrated = False
    while version < current:
        step = MIGRATIONS.get(version)
        if step is None:
            break
        record = step(record)
        version = record["schema_version"]
        migrated = True
    if version != current:
        emit("unusable", "schema-unmigratable"); continue
    if migrated and not dry:
        rewrite = git("notes", "--ref=" + notes_ref, "add", "-f", "-F", "-", sha, env=IDENT,
                      stdin=json.dumps(record, sort_keys=True).encode("utf-8", "surrogateescape"))
        if rewrite.returncode != 0:
            emit("unusable", "migration-rewrite-failed"); continue
    if migrated:
        emit("migrated", "", record)
    # The record must describe this ref, this commit and a path this script
    # would have chosen: never trust a recorded path on its own.
    parent = git("rev-parse", "--verify", "--quiet", sha + "^1")
    expected_trash = os.path.join(root, ".trash", match.group("base"))
    source = record.get("source")
    problems = []
    if record.get("ref") != ref:
        problems.append("its record names another ref")
    if record.get("stamp") != match.group("stamp"):
        problems.append("its record's stamp differs from the ref's")
    if record.get("trash") != expected_trash:
        problems.append("its record's trash path is not {}".format(expected_trash))
    if not isinstance(source, str) or hashlib.sha1(source.encode("utf-8", "surrogateescape")).hexdigest()[:10] != match.group("hash"):
        problems.append("its record's source does not hash to the ref's")
    if parent.returncode != 0 or record.get("head") != parent.stdout.decode().strip():
        problems.append("its record's head is not the archive commit's parent")
    tree = git("rev-parse", "--verify", "--quiet", sha + "^{tree}")
    if tree.returncode != 0 or record.get("tree") != tree.stdout.decode().strip():
        problems.append("its record's tree is not the archive commit's tree")
    if problems:
        emit("unusable", "record-invalid: " + "; ".join(problems), record); continue
    stamp = datetime.datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    if stamp.timestamp() >= cutoff:
        continue
    emit("expire", "", record)
with open(out, "w", encoding="utf-8", errors="surrogateescape") as handle:
    handle.write("".join(item + "\0" for item in plan))
PY
}

# Echo "<head>\x1f<branch>\x1f<locked 0|1>\x1f<lock reason>" for the worktree
# registered at <path>; return 1 when none is, 2 when the inventory is
# unreadable.
registered_worktree() { # <shared> <path>
  local listing field cur="" head="" branch="" locked=0 reason="" hit=1
  if ! listing="$(mktemp 2>"$ERRFILE")"; then return 2; fi
  if ! git -C "$1" worktree list --porcelain -z >"$listing" 2>"$ERRFILE"; then
    if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
    return 2
  fi
  while IFS= read -r -d '' field || [[ -n "$field" ]]; do
    case "$field" in
      "worktree "*)
        if [[ "$cur" == "$2" ]]; then hit=0; break; fi
        cur="${field#worktree }"; head=""; branch=""; locked=0; reason="" ;;
      "HEAD "*) head="${field#HEAD }" ;;
      "branch refs/heads/"*) branch="${field#branch refs/heads/}" ;;
      locked) locked=1 ;;
      "locked "*) locked=1; reason="${field#locked }" ;;
    esac
  done < "$listing"
  [[ "$cur" == "$2" ]] && hit=0
  if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
  (( hit == 0 )) || return 1
  printf '%s\x1f%s\x1f%s\x1f%s' "$head" "$branch" "$locked" "$reason"
}

#: The lock reason the archive path puts on every trash worktree: git keeps a
#: locked entry through `worktree prune`, and expiry accepts only this lock.
trash_lock_reason() { printf 'prune-worktrees archive %s' "$1"; }

# Every gate an expiring trash worktree must pass before it is destroyed:
# registered here at exactly its recorded path, not a symlink, locked with the
# sweep's own reason and no other, on
# its recorded HEAD and branch, idle for ARCHIVE_IDLE_HOURS, no process inside,
# and holding exactly the archived content. 0 = pass, 1 = keep (EXPIRE_WHY set),
# 2 = a read failed (EXPIRE_WHY set).
EXPIRE_WHY=""
trash_gates() { # <shared> <trash> <head> <branch|""> <tree> <ref>
  local entry rc=0 age real now_tree
  EXPIRE_WHY=""
  if ! real="$(cd "$2" 2>"$ERRFILE" && pwd -P)"; then EXPIRE_WHY="its trash worktree cannot be entered"; return 2; fi
  if [[ "$real" != "$2" ]]; then EXPIRE_WHY="its trash path resolves elsewhere (${real})"; return 1; fi
  entry="$(registered_worktree "$1" "$2")" || rc=$?
  case "$rc" in
    0) ;;
    1) EXPIRE_WHY="its trash path is not a worktree of this repository"; return 1 ;;
    *) EXPIRE_WHY="the worktree inventory cannot be read: $(tr '\n' ' ' < "$ERRFILE")"; return 2 ;;
  esac
  local e_head e_branch e_locked e_reason
  IFS=$'\x1f' read -r e_head e_branch e_locked e_reason <<<"$entry"
  if [[ "$e_locked" != 1 || "$e_reason" != "$(trash_lock_reason "$6")" ]]; then
    EXPIRE_WHY="its trash worktree is not locked by this sweep alone (someone unlocked or re-locked it)"; return 1
  fi
  if [[ "$e_head" != "$3" ]]; then EXPIRE_WHY="its trash worktree moved off the archived HEAD"; return 1; fi
  if [[ "$e_branch" != "$4" ]]; then EXPIRE_WHY="its trash worktree is on another branch"; return 1; fi
  if ! age="$(idle_hours "$2")"; then EXPIRE_WHY="its trash worktree's activity age cannot be read"; return 2; fi
  if (( age < ARCHIVE_IDLE_HOURS )); then EXPIRE_WHY="its trash worktree was written to ${age}h ago"; return 1; fi
  reprobe; rc=0; in_use "$2" || rc=$?
  case "$rc" in
    1) ;;
    0) EXPIRE_WHY="a process is working inside its trash worktree"; return 1 ;;
    *) EXPIRE_WHY="the process probe could not run"; return 1 ;;
  esac
  if ! now_tree="$(snapshot_tree "$2" "$3")"; then EXPIRE_WHY="its trash worktree cannot be snapshotted: $(tr '\n' ' ' < "$ERRFILE")"; return 2; fi
  if [[ "$now_tree" != "$5" ]]; then EXPIRE_WHY="its trash worktree holds content the archive lacks"; return 1; fi
  return 0
}

# Expire archive records past ARCHIVE_EXPIRE_DAYS. All checks come first; the
# destructive steps run in order — trash worktree, branch, ref — and a failure
# at any step keeps the archive ref. A dry run reports what a live run would do.
expire_archives() { # <shared> <abs_root> <dry 0|1>
  local plan
  if ! plan="$(mktemp 2>"$ERRFILE")"; then
    row failed "refs/archive/worktrees/" "" "cannot create a temp file for the expiry pass: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if ! plan_archives "$1" "$2" "$3" "$plan"; then
    row failed "refs/archive/worktrees/" "" "cannot read the archive records: $(tr '\n' ' ' < "$ERRFILE")"
    if ! rm -f "$plan"; then warn "could not remove temp file ${plan} — remove it by hand"; fi
    return 0
  fi
  local -a items=()
  local item
  while IFS= read -r -d '' item; do items+=("$item"); done < "$plan"
  if ! rm -f "$plan"; then warn "could not remove temp file ${plan} — remove it by hand"; fi
  local action ref sha reason record trash head branch tree rc tip del_branch
  for item in "${items[@]+"${items[@]}"}"; do
    IFS=$'\x1f' read -r action ref sha reason record <<<"$item"
    case "$action" in
      unusable) row archive-kept "$ref" "" "$reason" "$sha" ""; continue ;;
      migrated) row migrated "$ref" "" "" "$sha" ""; continue ;;
    esac
    if ! { IFS=$'\x1f' read -r -d '' trash head branch tree < <(python3 -c '
import json, sys
r = json.loads(sys.argv[1])
sys.stdout.write("\x1f".join([r["trash"], r["head"], r.get("branch") or "", r["tree"]]) + "\0")' "$record" 2>"$ERRFILE"); }; then
      row failed "$ref" "" "cannot read its record: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    # Gates first, nothing destroyed yet. The trash worktree must exist and
    # pass every gate: a missing one keeps the archive, never expires it.
    if [[ ! -e "$trash" && ! -L "$trash" ]]; then
      row archive-kept "$ref" "" "its trash worktree ${trash} is missing" "$sha" "$trash"; continue
    fi
    rc=0; trash_gates "$1" "$trash" "$head" "$branch" "$tree" "$ref" || rc=$?
    case "$rc" in
      0) ;;
      1) row archive-kept "$ref" "" "$EXPIRE_WHY" "$sha" "$trash"; continue ;;
      *) row failed "$ref" "" "expiry kept ${ref}: ${EXPIRE_WHY}"; continue ;;
    esac
    # The branch: absent is fine; a tip other than the recorded head is kept;
    # any git error keeps the whole archive.
    del_branch=""
    if [[ -n "$branch" ]]; then
      rc=0; git -C "$1" show-ref --verify --quiet "refs/heads/${branch}" 2>"$ERRFILE" || rc=$?
      case "$rc" in
        0) if ! tip="$(branch_tip "$1" "$branch")"; then
             row failed "$ref" "" "cannot read the tip of ${branch}, so ${ref} was kept: $(tr '\n' ' ' < "$ERRFILE")"; continue
           fi
           if [[ "$tip" == "$head" ]]; then del_branch="$branch"; else row branch-kept "$branch" "$branch" moved-since-archive; fi ;;
        1) ;;
        *) row failed "$ref" "" "cannot tell whether ${branch} exists, so ${ref} was kept: $(tr '\n' ' ' < "$ERRFILE")"; continue ;;
      esac
    fi
    if ! git -C "$1" notes --ref="$ARCHIVE_NOTES" show "$sha" >/dev/null 2>"$ERRFILE"; then
      row failed "$ref" "" "cannot re-read its record, so ${ref} was kept: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    if (( $3 )); then row expired "$ref" "$branch" "" "$sha" "$trash"; continue; fi
    # Destructive steps, in order: trash worktree, branch, ref, note. A failure
    # before the ref deletion keeps the ref and its record.
    # Immediately before the forced removal: a fresh probe, never the
    # snapshot trash_gates took, and a fresh fingerprint.
    local last_tree
    reprobe; rc=0; in_use "$trash" || rc=$?
    if (( rc != 1 )); then row archive-kept "$ref" "" "a process entered its trash worktree" "$sha" "$trash"; continue; fi
    if ! last_tree="$(snapshot_tree "$trash" "$head")"; then
      row failed "$ref" "" "cannot snapshot the trash worktree ${trash} before removal, so ${ref} was kept: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    if [[ "$last_tree" != "$tree" ]]; then row archive-kept "$ref" "" "its trash worktree changed after the gates" "$sha" "$trash"; continue; fi
    # The full identity again, last: a checkout to another commit with the same
    # tree would pass the fingerprint alone.
    local last_entry l_head l_branch l_locked l_reason lrc=0
    last_entry="$(registered_worktree "$1" "$trash")" || lrc=$?
    if (( lrc != 0 )); then
      row archive-kept "$ref" "" "its trash worktree's registration could not be re-read before removal" "$sha" "$trash"; continue
    fi
    IFS=$'\x1f' read -r l_head l_branch l_locked l_reason <<<"$last_entry"
    if [[ "$l_head" != "$head" || "$l_branch" != "$branch" || "$l_locked" != 1 || "$l_reason" != "$(trash_lock_reason "$ref")" ]]; then
      row archive-kept "$ref" "" "its trash worktree's HEAD, branch or lock changed after the gates" "$sha" "$trash"; continue
    fi
    # Twice forced: the trash worktree carries the sweep's own lock.
    if ! git -C "$1" worktree remove --force --force "$trash" 2>"$ERRFILE"; then
      row failed "$ref" "" "removing the trash worktree ${trash} failed, so ${ref} was kept: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    if [[ -n "$del_branch" ]]; then
      rc=0; delete_branch "$1" "$del_branch" "$head" || rc=$?
      if (( rc != 0 )); then
        report_branch_delete "$del_branch" "$head" "$rc"
        row failed "$ref" "" "its branch ${del_branch} could not be deleted, so ${ref} was kept"; continue
      fi
      row branch-deleted "$del_branch" "$del_branch" ""
    fi
    # The ref goes before its note, so a ref never exists without its record.
    # A run interrupted between the two leaves only an orphan note on an
    # unreferenced commit, which no reader takes for an archive and the orphan
    # pass removes.
    if ! git -C "$1" update-ref -d "$ref" "$sha" 2>"$ERRFILE"; then
      row failed "$ref" "" "deleting expired archive ${ref} failed, so it was kept with its record: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    if ! env "${GIT_IDENT[@]}" git -C "$1" notes --ref="$ARCHIVE_NOTES" remove "$sha" 2>"$ERRFILE"; then
      row failed "$ref" "" "${ref} was deleted but its record note could not be removed: $(tr '\n' ' ' < "$ERRFILE") — a later live run removes the orphan note"; continue
    fi
    row expired "$ref" "$branch" "" "$sha" "$trash"
  done
  return 0
}

#: A note on a commit no archive ref points at is an orphan (a run interrupted
#: between deleting a ref and its note). It is removed once the commit is
#: this many hours old, so an archive being written right now (its note lands
#: before its ref) is never mistaken for one.
ORPHAN_NOTE_GRACE_HOURS="${PRUNE_ORPHAN_NOTE_GRACE_HOURS:-24}"

# Remove orphan archive notes; a dry run only reports them.
remove_orphan_notes() { # <shared> <dry 0|1>
  local orphans
  if ! orphans="$(python3 - "$1" "$ARCHIVE_NOTES" "${PRUNE_NOW:-}" "$ORPHAN_NOTE_GRACE_HOURS" 2>"$ERRFILE" <<'PY'
import subprocess
import sys
import time

shared, notes_ref, now, grace = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
now = float(now) if now else time.time()


def git(*args):
    run = subprocess.run(["git", "-C", shared, *args], capture_output=True, text=True)
    return run


exists = git("rev-parse", "--verify", "--quiet", notes_ref)
if exists.returncode == 1:
    sys.exit(0)  # no notes ref yet: nothing to clean
if exists.returncode != 0:
    sys.stderr.write(exists.stderr)
    sys.exit(1)
notes = git("notes", "--ref=" + notes_ref, "list")
refs = git("for-each-ref", "--format=%(objectname)", "refs/archive/worktrees/")
for run in (notes, refs):
    if run.returncode != 0:
        sys.stderr.write(run.stderr)
        sys.exit(1)
held = set(refs.stdout.split())
for line in notes.stdout.splitlines():
    _, _, commit = line.partition(" ")
    if not commit or commit in held:
        continue
    when = git("log", "-1", "--format=%ct", commit)
    if when.returncode != 0:
        sys.stderr.write(when.stderr)
        sys.exit(1)
    if now - int(when.stdout.strip()) >= grace * 3600:
        print(commit)
PY
)"; then
    row failed "$ARCHIVE_NOTES" "" "cannot list orphan archive notes: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  local commit
  while IFS= read -r commit; do
    [[ -n "$commit" ]] || continue
    if (( ! $2 )) && ! env "${GIT_IDENT[@]}" git -C "$1" notes --ref="$ARCHIVE_NOTES" remove "$commit" 2>"$ERRFILE"; then
      row failed "$commit" "" "removing the orphan archive note on ${commit} failed: $(tr '\n' ' ' < "$ERRFILE")"; continue
    fi
    row orphan-note "$commit" "" "" "$commit" ""
  done <<<"$orphans"
  return 0
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
  if [[ "$merged" == unmerged ]]; then
    row branch-kept "$branch" "$branch" unmerged; return 0
  fi
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
    warn "$(cat "$ERRFILE") — cannot confirm origin's current default branch"
    return 1
  fi
  if ! read_origin_tips "$shared"; then
    warn "cannot read origin's current branch tips: $(tr '\n' ' ' < "$ERRFILE") — refusing to judge reachability from stale refs"
    return 1
  fi
  local db
  if ! db="$(default_branch_of "$shared" "$dry")"; then
    warn "cannot resolve origin's default branch — run \`git -C ${shared} remote set-head origin --auto\`"
    return 1
  fi
  if ! ORIGIN_DEFAULT="$(origin_default_tip "$shared" "$db")"; then
    warn "cannot read origin's current ${db}: $(cat "$ERRFILE") — refusing to judge merged-ness"
    return 1
  fi

  # Take both inventories up front: a failure inside a process substitution
  # would not reach the loop, and an empty inventory would read as "nothing
  # to prune" with exit 0 (rules/file-hygiene.md I/O Conventions). Nothing has
  # been decided yet, so an unreadable inventory is a precondition failure.
  local inventory branches
  inventory="$(mktemp)"; branches="$(mktemp)"
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
      warn "\`git worktree list --porcelain -z\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory worktrees"
    fi
    rm -f "$inventory" "$branches"
    return 1
  fi
  # Full refnames: `%(refname:short)` renders a local branch named like a
  # remote-tracking ref (origin/main) as heads/origin/main.
  if ! git -C "$shared" for-each-ref --format='%(refname)' refs/heads/ >"$branches" 2>"$ERRFILE"; then
    warn "\`git for-each-ref refs/heads/\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory branches"
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
          # A trash worktree belongs to its archive, missing or not: expiry
          # reports it and its branch stays held.
          if [[ "$path" == "$abs_root/.trash/"* ]]; then
            if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
            path=""; branch=""; detached=0; locked=0; lock_reason=""
            return 0
          fi
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
      # A trash worktree belongs to its archive; the expiry pass removes it.
      if [[ "$real" != "$abs_shared" && "$real" != "$abs_root/.trash/"* ]]; then
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

  expire_archives "$shared" "$abs_root" "$dry"
  remove_orphan_notes "$shared" "$dry"

  local rc=0
  python3 - "$abs_shared" "$db" "$dry" "$ROWS" <<'PY' || rc=$?
import json, sys
shared, db, dry, rows_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
result = {"shared": shared, "default_branch": db, "dry_run": dry, "worktrees_removed": [], "worktrees_archived": [],
          "archives_expired": [], "archives_kept": [], "archives_migrated": [], "orphan_notes_removed": [],
          "worktrees_kept": [], "branches_deleted": [], "branches_kept": [], "failed": []}
with open(rows_path, "rb") as handle:
    fields = handle.read().decode("utf-8", "surrogateescape").split("\0")
if fields and fields[-1] == "":
    fields.pop()
if len(fields) % 6:
    sys.stderr.write("prune-worktrees: decision rows are malformed; report this as a bug\n")
    sys.exit(2)
for index in range(0, len(fields), 6):
    kind, target, branch, reason, head, extra = fields[index:index + 6]
    if True:
        if kind == "removed":
            result["worktrees_removed"].append({"path": target, "branch": branch or None, "head": head})
        elif kind == "archived":
            ref, _, trash = extra.partition("\t")
            result["worktrees_archived"].append({"path": target, "branch": branch or None, "head": head,
                                                 "archive_ref": ref, "trash_path": trash or None})
        elif kind == "expired":
            result["archives_expired"].append({"ref": target, "head": head, "trash_path": extra or None,
                                               "branch": branch or None})
        elif kind == "archive-kept":
            result["archives_kept"].append({"ref": target, "head": head, "trash_path": extra or None,
                                            "reason": reason})
        elif kind == "orphan-note":
            result["orphan_notes_removed"].append(head)
        elif kind == "migrated":
            result["archives_migrated"].append({"ref": target, "head": head})
        elif kind == "kept":
            kept = {"path": target, "branch": branch or None, "reason": reason}
            if reason == "locked":
                kept["lock_reason"] = extra or None
            if reason == "archive-pending":
                kept["archive_ref"] = extra or None
            result["worktrees_kept"].append(kept)
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
