#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/prune-worktrees.sh.
#
# Real local git repos, driven offline: a bare "origin" plus a shared checkout
# cloned from it, per scenario, so the cases share no state and run in any
# order (rules/testing-standards.md Independence). WORKTREE_ROOT points at the
# temp dir so nothing lands in the operator's real ~/.worktrees.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Merged + clean      -> worktree removed, branch deleted.
#   2. Merged via origin   -> a branch whose commit landed on origin's default
#                             branch is removed after the fetch.
#   3. Unmerged            -> kept, reason unmerged; branch survives.
#   4. Dirty (untracked)   -> kept, reason dirty.
#   5. Dirty (modified)    -> kept, reason dirty.
#   6. Detached, unpushed  -> kept, reason detached.
#   7. Locked              -> kept, reason locked.
#   8. Outside the root    -> kept, reason outside-root, never removed.
#   9. Shared checkout     -> never listed, never removed; default branch kept.
#  10. Branch, no worktree -> merged one deleted, unmerged one kept.
#  11. Dry run             -> same decisions reported, nothing changes.
#  12. Stale metadata      -> a hand-deleted worktree dir is pruned.
#  13. Foreign worktree    -> a directory of ANOTHER repo under the root is
#                             untouched.
#  14. Usage / not a repo  -> exit 1, no JSON.
#  15. Fetch failure       -> exit 1, no JSON; nothing judged from stale refs.
#  16. Tool failure        -> a merge-base error is a failed row on stdout and
#                             stderr, exit 2, never a kept 'unmerged'.
#  17. Dry-run metadata    -> stale metadata and origin/HEAD survive a dry run;
#                             the remote default is still resolved.
#  18. Unenterable         -> a worktree the run cannot cd into is a failed
#                             row, exit 2 (skipped as root, who can enter anything).
#  19. Shadowing           -> a tag named like a branch, or a local branch
#                             named origin/main, cannot stand in for either operand.
#  20. Untraversable parent-> absence is not confirmed; failed row, metadata
#                             kept, exit 2 (skipped as root).
#  21. Raced branch        -> a tip that moved after its ancestry check is kept.
#  22. Half-done removal   -> a removal is reported even when its branch
#                             deletion then fails.
#  23. Deferred prunable   -> a prunable branch survives a skipped prune.
#  24. Newline path        -> a record is not split by a newline in the path
#                             (kept idle-unknown: the probe cannot match it).
#  25. Branch config       -> a deleted branch's branch.<name> config goes too.
#  26. Locked and gone     -> git keeps its metadata, so its branch is kept too.
#  27. No -z               -> a git without `-z` decides nothing at all.
#  28. Dry-run deferral    -> a preview defers what the live run would defer.
#  29. Config sibling      -> branch.<name>.<key> of a LONGER branch name is
#                             not read as this branch's config.
#  30. Claimed branch      -> a branch a worktree holds is kept, not deleted.
#  31. Claimed mid-delete  -> a worktree claiming it inside the deletion's own
#                             window gets the branch back.
#  32. Newline parent      -> absence is confirmed through a parent whose own
#                             name ends in a newline.
#  33. Unreadable recheck   -> a failed post-deletion occupancy read is a
#                             failure, never an unoccupied answer.
#  35. Reachable, idle      -> a clean detached worktree on a pushed commit is
#                             removed; a pushed unmerged branch goes with it.
#  36. In use               -> a process with its cwd inside keeps it.
#  37. Unpushed, idle       -> archived under refs/archive/worktrees/ with its
#                             commits and an untracked file, then moved to
#                             the root's .trash/.
#  38. Locked, idle         -> kept, reported with its lock reason.
#  39. Not idle             -> fresh activity keeps a reachable worktree.
#  40. Idle, below archive  -> an unpushed one idle past the removal window but
#                             not the archive window is kept.
#  41. No process probe     -> lsof missing keeps it as idle-unknown.
#  42. Archive fails        -> the worktree is kept and the failure reported.
#  43. Dry run              -> reachable and archive candidates are previewed,
#                             nothing is removed and no archive ref is written.
#  44. Merged, not idle     -> a clean merged worktree with fresh activity is kept.
#  45. Merged, in use       -> a process inside keeps a clean merged worktree.
#  46. Fresh edit           -> a tracked file edited now keeps an old worktree.
#  47. Changed at removal   -> a process arriving before the removal keeps it.
#  48. Changed after archive-> the archive is reported, the worktree kept.
#  49. Move fails           -> a written archive is still reported; the next
#                             run never stacks another (archive-pending).
#  37b. Trash              -> a moved worktree is not judged by the next run.
#  50. Archive expiry       -> refs past the expiry window go with their trash
#                             worktree and branch; a dry run previews; a
#                             recent, unstamped or unknown-schema ref stays.
#  51. Dirty submodule      -> kept, never archived.
#  52. Same basename        -> two worktrees archive to two refs.
#  53-57. Expiry gates      -> a newer schema, a record naming another
#                             worktree, content the archive lacks, a lock,
#                             or a process inside keeps the archive and trash.
#  58. Missing trash        -> its archive, registration and branch are kept.
#  59. Newline path         -> lsof cannot report it faithfully: never idle.
#  60. Other remote         -> a ref of a remote other than origin proves nothing.
#  61. Proof gone           -> reachability re-derived just before removal.
#  62. Gitlink, embedded    -> a dirty gitlink without .gitmodules, and an
#                             embedded repository (untracked, ignored, or below
#                             an untracked directory), keep the worktree.
#  63. Unusable records     -> schema_version 0, or unparseable: kept.
#  64. Twin archives        -> one parent and tree in one second: two commits,
#                             two records.
#  65. Foreign ref name     -> a slash in the name part: never planned.
#  66. Late process         -> a fresh probe before the forced trash removal.
#  67. Bool schema          -> schema_version true is unparseable.
#  68. Interrupted expiry   -> ref gone, orphan note left; the next run
#                             removes the orphan.
#  69. Dry/live agreement   -> both judge origin as it is now.
#  70. Symlinked .trash     -> the candidate is kept, nothing archived.
#  71. Force-push           -> a merge dropped before the removal keeps it.
#  72. Credential URL       -> a failed remote command never relays the URL.
#  73. Same-tree checkout   -> the full identity is re-read before the forced
#                             trash removal.
#  74. Ignored inventory    -> an ignored file changed in the trash keeps it.
#  75. Trash nested repo    -> an embedded repository in the trash keeps it.
#  76. .trash swapped       -> a move that lands outside the root comes back.
#  77. Symlinked .git       -> a trash worktree with one keeps its archive.
#
# Run: bash skills/herdr-foreman/tests/test_prune_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() {
  if [[ -n "${SLEEPER:-}" ]] && kill -0 "$SLEEPER" 2>/dev/null; then kill "$SLEEPER" || echo "warn: could not stop sleeper $SLEEPER" >&2; fi
  [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2
  return 0
}
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_repo() { # <prefix> -> sets SHARED, SEED, BARE
  local prefix="$1"
  BARE="$TMP/${prefix}.git"
  SEED="$TMP/${prefix}-seed"
  git init -q --bare -b main "$BARE"            || die "git init --bare failed"
  git clone -q "$BARE" "$SEED" 2>/dev/null      || die "git clone failed"
  printf 'x\n' > "$SEED/f"                      || die "seed write failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t add f  || die "git add failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t commit -q -m c1 || die "git commit failed"
  git -C "$SEED" push -q origin main            || die "git push failed"
  SHARED="$TMP/${prefix}-shared"
  git clone -q "$BARE" "$SHARED" 2>/dev/null    || die "git clone (shared) failed"
  git -C "$SHARED" remote set-head origin --auto >/dev/null 2>&1 \
    || die "git remote set-head failed"
}

add_wt() { # <shared> <branch> <path>  (cut at main)
  git -C "$1" worktree add -q -b "$2" "$3" origin/main 2>/dev/null || die "worktree add $2 failed"
}

commit_in() { # <worktree> <file>
  printf 'y\n' > "$1/$2" || die "write $2 failed"
  git -C "$1" -c user.name=t -c user.email=t@t add "$2" || die "git add in $1 failed"
  git -C "$1" -c user.name=t -c user.email=t@t commit -q -m "c-$2" || die "git commit in $1 failed"
}

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

# jq-free field readers over $OUT.
removed_paths() { python3 -c 'import json,sys; print("\n".join(r["path"] for r in json.load(sys.stdin)["worktrees_removed"]))' <<<"$OUT"; }
kept_reason() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["reason"] for r in d["worktrees_kept"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
branches_deleted() { python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["branches_deleted"]))' <<<"$OUT"; }
branch_kept_reason() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["reason"] for r in d["branches_kept"] if r["branch"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1" <<<"$OUT"; }
mentions_path() { python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(r["path"]==sys.argv[1] for r in d["worktrees_kept"]+d["worktrees_removed"]) else 1)' "$1" <<<"$OUT"; }
has_branch() { # <shared> <branch> -> 0 present, 1 absent; a git error aborts the harness
  local rc=0
  git -C "$1" show-ref --verify --quiet "refs/heads/$2" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "git show-ref failed (exit $rc) for $2 in $1" ;; esac
}
listed() { # <shared> <path>  -> 0 listed, 1 not listed; a tool failure aborts the harness
  local inventory rc=0
  inventory="$(git -C "$1" worktree list --porcelain)" || die "git worktree list failed in $1"
  grep -qxF "worktree $2" <<<"$inventory" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "grep failed (exit $rc) reading the worktree inventory" ;; esac
}


# Age a worktree: its directory and its gitdir's HEAD, index and logs/HEAD all
# last written on 2020-01-01. IDLE_NOW is nine days later, fixed.
IDLE_NOW=1578614400
age_wt() { # <worktree>
  local gitdir f
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir)" || die "rev-parse --absolute-git-dir failed in $1"
  for f in "$gitdir/HEAD" "$gitdir/index" "$gitdir/logs/HEAD"; do
    if [[ -e "$f" ]]; then touch -t 202001010000 "$f" || die "touch $f failed"; fi
  done
  find "$1" -path "$1/.git" -prune -o -exec touch -h -t 202001010000 {} + || die "touch the files of $1 failed"
}
idle_run() { # <extra env...> -- runs the script on $SHARED with the fixed clock
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$IDLE_NOW" "$@" bash "$SCRIPT" "$SHARED" "${IDLE_ARGS[@]+"${IDLE_ARGS[@]}"}" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}
archived_ref() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["archive_ref"] for r in d["worktrees_archived"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
lock_reason_of() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r.get("lock_reason") or "" for r in d["worktrees_kept"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
removed_head() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["head"] for r in d["worktrees_removed"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }

# An lsof stand-in: silent until its <n>th call, then reporting a process
# working inside <path>.
lsof_turns_busy() { # <dir> <n> <path>
  mkdir -p "$1" || die "mkdir $1 failed"
  # shellcheck disable=SC2016  # The stand-in's $(...) must run in the stand-in, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf x >> %q\nif (( $(wc -c < %q) >= %s )); then printf "p1\\0\\nfcwd\\0n%%s\\0\\n" %q; fi\n' \
    "$1/calls" "$1/calls" "$2" "$3" > "$1/lsof" || die "write lsof stand-in failed"
  chmod +x "$1/lsof" || die "chmod lsof stand-in failed"
}
trash_of() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["trash_path"] or "" for r in d["worktrees_archived"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
# Write a note <json> on the commit <ref> points at (a hand-made record).
set_record() { # <shared> <ref> <json>
  git -C "$1" -c user.name=t -c user.email=t@t notes --ref=refs/notes/worktree-archive add -f -m "$3" "$2" \
    || die "notes add failed for $2"
}
record_of() { # <shared> <ref>
  git -C "$1" notes --ref=refs/notes/worktree-archive show "$2" || die "notes show failed for $2"
}
archives_kept_reason() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["reason"] for r in d["archives_kept"] if r["ref"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
expired_refs() { python3 -c 'import json,sys; print(",".join(sorted(r["ref"] for r in json.load(sys.stdin)["archives_expired"])))' <<<"$OUT"; }
# Archive one idle unpushed worktree named <name> in $SHARED and echo "<ref> <trash>".
archive_one() { # <name> <branch>
  local wt="$ROOT/$1"
  add_wt "$SHARED" "$2" "$wt"; commit_in "$wt" "$1.txt"
  printf 'kept work\n' > "$wt/untracked-$1" || die "untracked write failed"
  age_wt "$wt"
  idle_run
  local ref trash
  ref="$(archived_ref "$wt")"; trash="$(trash_of "$wt")"
  [[ -n "$ref" && -n "$trash" ]] || die "archive_one $1: no archive, out=$OUT err=$ERRTEXT"
  printf '%s %s' "$ref" "$trash"
}
# 31 days after IDLE_NOW: every archive written at IDLE_NOW is past expiry.
LATER_NOW=$((1578614400 + 31 * 86400))
later_run() {
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$LATER_NOW" bash "$SCRIPT" "$SHARED" "${IDLE_ARGS[@]+"${IDLE_ARGS[@]}"}" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}
# Age a trash worktree to before LATER_NOW's archive window.
age_trash() { # <trash>
  local gitdir
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir)" || die "rev-parse trash gitdir failed"
  local f; for f in "$gitdir/HEAD" "$gitdir/index" "$gitdir/logs/HEAD"; do
    if [[ -e "$f" ]]; then touch -t 202001100000 "$f" || die "touch $f failed"; fi
  done
  find "$1" -path "$1/.git" -prune -o -exec touch -h -t 202001100000 {} + || die "touch trash files failed"
}


has_note() { # <shared> <commit> -> 0 a note exists, 1 none; any other failure aborts the harness
  local rc=0
  git -C "$1" notes --ref=refs/notes/worktree-archive list "$2" >/dev/null 2>"$TMP/has_note.err" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) grep -q "no note found" "$TMP/has_note.err" && return 1
       die "git notes list exited 1 without 'no note found': $(cat "$TMP/has_note.err")" ;;
    *) die "git notes list failed (exit $rc): $(cat "$TMP/has_note.err")" ;;
  esac
}

# Stop the background sleeper: SIGTERM, then its exit status must be 143
# (128 + SIGTERM); anything else means the fixture did not behave as assumed.
stop_sleeper() {
  local st=0
  kill "$SLEEPER" || die "could not stop the sleeper $SLEEPER"
  wait "$SLEEPER" || st=$?
  case "$st" in
    143) SLEEPER="" ;;
    *) die "the sleeper $SLEEPER ended with status $st, not 143 (SIGTERM)" ;;
  esac
}
main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/prune-worktrees.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  # The script reports physical paths; macOS mktemp hands out a symlinked /var.
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT
  # The operator's git config never reaches the script under test: no global
  # identity, as on a CI runner, so every commit the script writes must carry
  # its own. Fixtures pass theirs with -c.
  : > "$TMP/gitconfig" || die "cannot create an empty global git config"
  export GIT_CONFIG_GLOBAL="$TMP/gitconfig" GIT_CONFIG_NOSYSTEM=1
  ROOT="$TMP/worktrees"
  mkdir -p "$ROOT" || die "mkdir root failed"

  # --- 1, 3, 4, 5, 6, 7, 9, 10 share one repo: one run decides them all.
  mk_repo one
  add_wt "$SHARED" review/merged "$ROOT/one-merged"
  add_wt "$SHARED" test/unmerged "$ROOT/one-unmerged"; commit_in "$ROOT/one-unmerged" u
  add_wt "$SHARED" test/untracked "$ROOT/one-untracked"; printf 'z\n' > "$ROOT/one-untracked/scratch" || die "write failed"
  git -C "$SHARED" config status.showUntrackedFiles no || die "config failed"
  add_wt "$SHARED" test/modified "$ROOT/one-modified"; printf 'changed\n' > "$ROOT/one-modified/f" || die "write failed"
  git -C "$SHARED" worktree add -q --detach "$ROOT/one-detached" origin/main 2>/dev/null || die "detached add failed"
  commit_in "$ROOT/one-detached" d
  add_wt "$SHARED" test/locked "$ROOT/one-locked"; git -C "$SHARED" worktree lock "$ROOT/one-locked" || die "lock failed"
  git -C "$SHARED" branch --no-track merged-no-wt origin/main || die "branch failed"
  git -C "$SHARED" branch --no-track unmerged-no-wt origin/main || die "branch failed"
  git -C "$SHARED" worktree add -q "$TMP/one-scratch" unmerged-no-wt 2>/dev/null || die "scratch add failed"
  commit_in "$TMP/one-scratch" w
  git -C "$SHARED" worktree remove "$TMP/one-scratch" || die "scratch remove failed"

  run "$SHARED"
  echo "1. merged + clean worktree is removed and its branch deleted"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/one-merged"* ]] && [[ ! -e "$ROOT/one-merged" ]] && ! has_branch "$SHARED" review/merged; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "3. unmerged worktree is kept with reason unmerged"
  if [[ "$(kept_reason "$ROOT/one-unmerged")" == unmerged ]] && [[ -d "$ROOT/one-unmerged" ]] && has_branch "$SHARED" test/unmerged; then pass; else fail "out=$OUT"; fi
  echo "4. untracked file keeps the worktree as dirty, even with status.showUntrackedFiles=no"
  if [[ "$(kept_reason "$ROOT/one-untracked")" == dirty ]] && [[ -f "$ROOT/one-untracked/scratch" ]]; then pass; else fail "out=$OUT"; fi
  echo "5. modified file keeps the worktree as dirty"
  if [[ "$(kept_reason "$ROOT/one-modified")" == dirty ]] && [[ -d "$ROOT/one-modified" ]]; then pass; else fail "out=$OUT"; fi
  echo "6. a detached worktree holding an unpushed commit is kept"
  if [[ "$(kept_reason "$ROOT/one-detached")" == detached ]] && [[ -d "$ROOT/one-detached" ]]; then pass; else fail "out=$OUT"; fi
  echo "7. locked worktree is kept"
  if [[ "$(kept_reason "$ROOT/one-locked")" == locked ]] && [[ -d "$ROOT/one-locked" ]]; then pass; else fail "out=$OUT"; fi
  echo "9. the shared checkout is never listed and the default branch survives"
  if ! mentions_path "$SHARED" && has_branch "$SHARED" main && [[ "$(field default_branch)" == main ]]; then pass; else fail "out=$OUT"; fi
  echo "10. merged branch without a worktree is deleted; unmerged one is kept"
  if [[ "$(branches_deleted)" == *merged-no-wt* ]] && ! has_branch "$SHARED" merged-no-wt && [[ "$(branch_kept_reason unmerged-no-wt)" == unmerged ]] && has_branch "$SHARED" unmerged-no-wt; then pass; else fail "out=$OUT"; fi

  # --- 2. merged via origin: commit on a branch, land it on origin main, prune.
  mk_repo two
  add_wt "$SHARED" feat/landed "$ROOT/two-landed"; commit_in "$ROOT/two-landed" landed
  git -C "$ROOT/two-landed" push -q origin feat/landed || die "push failed"
  git -C "$SEED" fetch -q origin || die "seed fetch failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t merge -q --no-ff origin/feat/landed -m merge || die "seed merge failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  run "$SHARED"
  echo "2. a branch landed on origin's default branch is removed after the fetch"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/two-landed"* ]] && ! has_branch "$SHARED" feat/landed; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 8. outside the root.
  mk_repo eight
  git -C "$SHARED" worktree add -q -b review/outside "$TMP/eight-outside" origin/main 2>/dev/null || die "outside add failed"
  run "$SHARED"
  echo "8. a worktree outside the root is kept and reported"
  if (( RC == 0 )) && [[ "$(kept_reason "$TMP/eight-outside")" == outside-root ]] && [[ -d "$TMP/eight-outside" ]] && has_branch "$SHARED" review/outside; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 11. dry run.
  mk_repo eleven
  add_wt "$SHARED" review/dry "$ROOT/eleven-dry"
  git -C "$SHARED" branch --no-track dry-no-wt origin/main || die "branch failed"
  run "$SHARED" --dry-run
  echo "11. dry run reports the decisions and changes nothing"
  if (( RC == 0 )) && [[ "$(field dry_run)" == True ]] && [[ "$(removed_paths)" == *"$ROOT/eleven-dry"* ]] && [[ "$(branches_deleted)" == *dry-no-wt* ]] && [[ -d "$ROOT/eleven-dry" ]] && has_branch "$SHARED" review/dry && has_branch "$SHARED" dry-no-wt; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 12. stale metadata.
  mk_repo twelve
  add_wt "$SHARED" review/gone "$ROOT/twelve-gone"
  rm -rf "$ROOT/twelve-gone" || die "rm failed"
  run "$SHARED"
  echo "12. a hand-deleted worktree's metadata is pruned and its merged branch deleted in the same run"
  if (( RC == 0 )) && ! listed "$SHARED" "$ROOT/twelve-gone" && [[ "$(kept_reason "$ROOT/twelve-gone")" == prunable ]] && [[ "$(branches_deleted)" == *review/gone* ]] && ! has_branch "$SHARED" review/gone; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 13. foreign worktree under the root.
  mk_repo thirteen
  local foreign_shared="$SHARED"
  mk_repo other
  add_wt "$SHARED" review/other "$ROOT/thirteen-other"
  run "$foreign_shared"
  echo "13. another repository's worktree under the root is untouched"
  if (( RC == 0 )) && [[ -d "$ROOT/thirteen-other" ]] && [[ "$OUT" != *thirteen-other* ]]; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 15. an unreachable origin is a precondition failure: nothing is judged from stale refs.
  mk_repo fifteen
  add_wt "$SHARED" review/stale "$ROOT/fifteen-stale"
  git -C "$SHARED" remote set-url origin "$TMP/nowhere.git" || die "set-url failed"
  run "$SHARED"
  echo "15. a failed fetch is exit 1 with no JSON and the merged worktree untouched"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"stale refs"* ]] && [[ -d "$ROOT/fifteen-stale" ]] && has_branch "$SHARED" review/stale; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 16. a merge-base tool failure is a failed row on stderr and stdout, exit 2, never "unmerged".
  mk_repo sixteen
  add_wt "$SHARED" review/broken "$ROOT/sixteen-broken"
  git -C "$SHARED" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/vanished || die "symbolic-ref failed"
  git -C "$SEED" push -q origin main:refs/heads/vanished || die "push vanished failed"
  git -C "$SEED" symbolic-ref HEAD refs/heads/vanished || die "seed symbolic-ref failed"
  git -C "$BARE" symbolic-ref HEAD refs/heads/vanished || die "bare HEAD failed"
  git -C "$SHARED" fetch -q origin || die "fetch failed"
  # origin's default is now `vanished`; a git shim makes merge-base itself fail
  # (exit 128), the tool failure this case is about.
  mkdir -p "$TMP/shim" || die "mkdir shim failed"
  cat > "$TMP/shim/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in *merge-base*) echo "fatal: simulated merge-base failure" >&2; exit 128 ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim/git" || die "chmod shim failed"
  run_with_shim() { RUN_SEQ=$((RUN_SEQ+1)); OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"; }
  run_with_shim
  echo "16. a merge-base tool failure lands in failed, on stderr, exit 2, worktree untouched"
  if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*merge-base* ]] && [[ "$ERRTEXT" == *"merge-base failed"* ]] && [[ "$(kept_reason "$ROOT/sixteen-broken")" == "" ]] && [[ -d "$ROOT/sixteen-broken" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 17. dry run leaves stale metadata and remote-tracking refs in place.
  mk_repo seventeen
  add_wt "$SHARED" review/preview "$ROOT/seventeen-preview"
  rm -rf "$ROOT/seventeen-preview" || die "rm failed"
  git -C "$SHARED" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/elsewhere || die "symbolic-ref failed"
  head_before="$(cat "$SHARED/.git/refs/remotes/origin/HEAD")" || die "read HEAD failed"
  run "$SHARED" --dry-run
  echo "17. dry run prunes no metadata, rewrites no origin/HEAD, and still finds the remote default"
  if (( RC == 0 )) && listed "$SHARED" "$ROOT/seventeen-preview" && [[ "$(cat "$SHARED/.git/refs/remotes/origin/HEAD")" == "$head_before" ]] && [[ "$(field default_branch)" == main ]] && [[ "$(kept_reason "$ROOT/seventeen-preview")" == prunable ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 18. an unenterable worktree is a failed row, exit 2, never prunable.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo eighteen
    add_wt "$SHARED" review/sealed "$ROOT/eighteen-sealed"
    chmod 000 "$ROOT/eighteen-sealed" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/eighteen-sealed" || die "chmod restore failed"
    echo "18. a worktree that cannot be entered is a failed row on stderr, exit 2"
    if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*"cannot enter"* ]] && [[ "$ERRTEXT" == *"cannot enter"* ]] && [[ "$ERRTEXT" == *"skipping"* ]] && has_branch "$SHARED" review/sealed && listed "$SHARED" "$ROOT/eighteen-sealed"; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 19. a merged tag named like an unmerged branch must not shadow it.
  mk_repo nineteen
  add_wt "$SHARED" review/shadow "$ROOT/nineteen-shadow"; commit_in "$ROOT/nineteen-shadow" s
  git -C "$SHARED" tag review/shadow origin/main || die "tag failed"
  git -C "$SHARED" branch --no-track origin/main origin/main 2>/dev/null || die "shadow branch failed"
  run "$SHARED"
  echo "19. ancestry is judged on fully qualified refs: a same-name tag or an origin/main local branch cannot shadow"
  if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/nineteen-shadow")" == unmerged ]] && [[ -d "$ROOT/nineteen-shadow" ]] && has_branch "$SHARED" review/shadow; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 20. an ancestor that denies traversal is not "gone".
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twenty
    mkdir -p "$ROOT/twenty-parent" || die "mkdir failed"
    add_wt "$SHARED" review/hidden "$ROOT/twenty-parent/hidden"
    chmod 000 "$ROOT/twenty-parent" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/twenty-parent" || die "chmod restore failed"
    echo "20. a worktree behind an untraversable parent is a failed row, metadata and branch kept, exit 2"
    if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*"cannot confirm"* ]] && [[ "$ERRTEXT" == *"skipping"* ]] && has_branch "$SHARED" review/hidden && listed "$SHARED" "$ROOT/twenty-parent/hidden"; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 21. a branch that moves between its ancestry check and the deletion is kept.
  mk_repo twentyone
  add_wt "$SHARED" review/racing "$ROOT/twentyone-racing"
  # Advance origin/main so the shim has a second merged commit to move to.
  commit_in "$SEED" second
  git -C "$SEED" push -q origin main || die "push failed"
  git -C "$SHARED" fetch -q origin || die "fetch failed"
  mkdir -p "$TMP/shim21" || die "mkdir shim failed"
  cat > "$TMP/shim21/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
# Move the branch on the THIRD read of its tip — the first judges it, the
# second is the pre-removal recheck, the third comes after the worktree is gone
# (so the move is allowed) and before the compare-and-delete. The new tip is
# merged too, so only the guard can keep the branch. A move that fails breaks
# the fixture's premise: say so and stop rather than let the run pass.
case "\$*" in *"refs/heads/review/racing"*)
  printf 'x' >> "$TMP/shim21/reads"
  if [[ "\$(cat "$TMP/shim21/reads")" == xxx ]]; then
    # The move's own chatter must not reach stdout: the caller is capturing it
    # as the branch tip.
    if ! "$(command -v git)" -C "$SHARED" branch -f review/racing refs/remotes/origin/main >&2; then
      echo "shim21: fixture could not move review/racing" >&2
      exit 1
    fi
  fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim21/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim21:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "21. a branch that moved after its ancestry check is kept, its removal still reported, exit 2"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$ROOT/twentyone-racing"* ]] && [[ "$OUT" == *"moved after its ancestry check"* ]] && has_branch "$SHARED" review/racing; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 22. a worktree removal whose branch deletion fails still reports the removal.
  mk_repo twentytwo
  add_wt "$SHARED" review/halfway "$ROOT/twentytwo-halfway"
  mkdir -p "$TMP/shim22" || die "mkdir shim failed"
  cat > "$TMP/shim22/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in *"update-ref -d"*) echo "fatal: fixture refuses the deletion" >&2; exit 1 ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim22/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim22:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "22. the JSON reports the removal that happened even when the branch deletion fails"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$ROOT/twentytwo-halfway"* ]] && [[ ! -e "$ROOT/twentytwo-halfway" ]] && [[ "$OUT" == *'"failed": [{'*"deleting"* ]] && [[ "$OUT" == *"fixture refuses the deletion"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 23. a prunable branch is deferred when the metadata prune is skipped.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twentythree
    add_wt "$SHARED" review/vanished "$ROOT/twentythree-vanished"
    rm -rf "$ROOT/twentythree-vanished" || die "rm failed"
    add_wt "$SHARED" review/sealed23 "$ROOT/twentythree-sealed"
    chmod 000 "$ROOT/twentythree-sealed" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/twentythree-sealed" || die "chmod restore failed"
    echo "23. a prunable branch is not deleted while its metadata survives a skipped prune"
    if (( RC == 2 )) && [[ "$(kept_reason "$ROOT/twentythree-vanished")" == prunable ]] && [[ "$(branches_deleted)" != *review/vanished* ]] && has_branch "$SHARED" review/vanished; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 24. a worktree path holding a newline is one field, not two.
  mk_repo twentyfour
  newline_path="$ROOT/twentyfour-$(printf 'a\nb')"
  git -C "$SHARED" worktree add -q -b review/newline "$newline_path" origin/main \
    || die "fixture could not create a worktree at a newline-bearing path"
  run "$SHARED"
  # The process probe cannot report such a path faithfully, so it is kept;
  # the point here is that it is reported whole, as one record.
  echo "24. a newline in a worktree path does not split its record"
  if (( RC == 0 )) && [[ "$OUT" != *'"path": "'"$ROOT"'/twentyfour-a"'* ]] && [[ "$(kept_reason "$newline_path")" == idle-unknown ]] \
    && has_branch "$SHARED" review/newline; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 25. a tracked branch's config goes with it.
  mk_repo twentyfive
  git -C "$SHARED" worktree add -q --track -b review/tracked "$ROOT/twentyfive-tracked" origin/main 2>/dev/null \
    || die "fixture could not create a tracking worktree"
  git -C "$SHARED" config --get-regexp '^branch\.review/tracked\.' >/dev/null || die "fixture branch has no tracking config"
  run "$SHARED"
  echo "25. a deleted branch leaves no stale branch.<name> config behind"
  cfg_rc=0
  git -C "$SHARED" config --get-regexp '^branch\.review/tracked\.' >/dev/null 2>"$TMP/cfg.err" || cfg_rc=$?
  (( cfg_rc == 0 || cfg_rc == 1 )) || die "git config failed (exit $cfg_rc): $(cat "$TMP/cfg.err")"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/twentyfive-tracked"* ]] && (( cfg_rc == 1 )); then pass; else fail "rc=$RC cfg_rc=$cfg_rc out=$OUT err=$ERRTEXT"; fi

  # --- 26. a locked entry whose directory is gone keeps its branch.
  mk_repo twentysix
  add_wt "$SHARED" review/lockedgone "$ROOT/twentysix-lockedgone"
  git -C "$SHARED" worktree lock "$ROOT/twentysix-lockedgone" || die "lock failed"
  rm -rf "$ROOT/twentysix-lockedgone" || die "rm failed"
  run "$SHARED"
  echo "26. a locked entry git's prune preserves does not release its branch"
  if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/twentysix-lockedgone")" == locked ]] && [[ "$(branches_deleted)" != *review/lockedgone* ]] && has_branch "$SHARED" review/lockedgone; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 27. without -z there is no unambiguous inventory, so nothing is decided.
  # No newline path is needed: the line-oriented form cannot be trusted at all,
  # since a path whose tail reads as an attribute passes any scan (#410).
  mk_repo twentyseven
  add_wt "$SHARED" review/plain "$ROOT/twentyseven-plain"
  mkdir -p "$TMP/shim27" || die "mkdir shim failed"
  cat > "$TMP/shim27/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
# Stand in for a git older than 2.36, which has no -z on this subcommand.
case "\$*" in *"worktree list"*-z*) echo "error: unknown option z" >&2; exit 129 ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim27/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim27:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "27. a git without -z refuses the inventory, deciding nothing"
  plain_dir=0; [[ -d "$ROOT/twentyseven-plain" ]] && plain_dir=1
  plain_branch=0; has_branch "$SHARED" review/plain && plain_branch=1
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"cannot be listed unambiguously"* ]] && (( plain_dir )) && (( plain_branch )); then pass; else fail "rc=$RC dir=$plain_dir branch=$plain_branch out=$OUT err=$ERRTEXT"; fi

  # --- 28. a dry run previews the deferral a live run would make.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twentyeight
    add_wt "$SHARED" review/previewgone "$ROOT/twentyeight-gone"
    rm -rf "$ROOT/twentyeight-gone" || die "rm failed"
    add_wt "$SHARED" review/sealed28 "$ROOT/twentyeight-sealed"
    chmod 000 "$ROOT/twentyeight-sealed" || die "chmod failed"
    run "$SHARED" --dry-run
    chmod 755 "$ROOT/twentyeight-sealed" || die "chmod restore failed"
    echo "28. a dry run does not promise a deletion the live run would defer"
    if (( RC == 2 )) && [[ "$(branches_deleted)" != *review/previewgone* ]] && has_branch "$SHARED" review/previewgone; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 29. a longer branch's config is not this branch's config.
  mk_repo twentynine
  # --no-track: a tracking branch has its own branch.<name>.* section, and
  # removing that would succeed whether or not the probe matched the sibling.
  git -C "$SHARED" branch --no-track review/foo origin/main || die "branch failed"
  # Section `review/foo.bar`, key `remote` — `branch.review/foo.` prefixes it,
  # but it belongs to another branch entirely.
  git -C "$SHARED" config "branch.review/foo.bar.remote" origin || die "config failed"
  run "$SHARED"
  echo "29. a sibling branch's config section is not mistaken for this branch's"
  sibling_kept=0
  config_rc=0
  git -C "$SHARED" config --get "branch.review/foo.bar.remote" >/dev/null || config_rc=$?
  case "$config_rc" in 0) sibling_kept=1 ;; 1) ;; *) die "git config --get failed (exit $config_rc)" ;; esac
  if (( RC == 0 )) && [[ "$(branches_deleted)" == *review/foo* ]] && ! has_branch "$SHARED" review/foo && (( sibling_kept )); then pass; else fail "rc=$RC sibling=$sibling_kept out=$OUT err=$ERRTEXT"; fi

  # --- 30. a branch a worktree holds is kept even when the inventory missed it.
  mk_repo thirty
  add_wt "$SHARED" review/claimed "$ROOT/thirty-claimed"
  mkdir -p "$TMP/shim30" || die "mkdir shim failed"
  # The run's own inventory read comes back empty, so the branch reaches the
  # branch pass as if no worktree held it; every later read is the real thing.
  cat > "$TMP/shim30/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"worktree list"*-z*)
    if [[ ! -e "$TMP/shim30/seen" ]]; then : > "$TMP/shim30/seen"; exit 0; fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim30/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim30:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "30. a branch checked out in a worktree the inventory missed is kept"
  if (( RC == 0 )) && [[ "$(branch_kept_reason review/claimed)" == checked-out ]] && has_branch "$SHARED" review/claimed && [[ -d "$ROOT/thirty-claimed" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 31. a worktree claiming the branch inside the deletion window.
  mk_repo thirtyone
  git -C "$SHARED" branch review/raced origin/main || die "branch failed"
  mkdir -p "$TMP/shim31" || die "mkdir shim failed"
  # `update-ref -d` has no checked-out guard: the shim claims the branch just
  # before the deletion lands, exactly the race the inventory cannot see.
  cat > "$TMP/shim31/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"update-ref -d refs/heads/review/raced"*)
    if [[ ! -e "$TMP/shim31/seen" ]]; then
      : > "$TMP/shim31/seen"
      if ! "$(command -v git)" -C "$SHARED" worktree add -q "$ROOT/thirtyone-raced" review/raced >/dev/null; then
        echo "shim31: fixture could not claim review/raced" >&2
      fi
    fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim31/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim31:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "31. a branch claimed while it was being deleted is put back"
  if (( RC == 2 )) && has_branch "$SHARED" review/raced && [[ "$OUT" == *"was restored at"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 33. a post-deletion occupancy read that fails is not "nothing holds it".
  mk_repo thirtythree
  git -C "$SHARED" branch --no-track review/unreadable origin/main || die "branch failed"
  mkdir -p "$TMP/shim33" || die "mkdir shim failed"
  # The reads before the deletion answer; the one after it fails, so the run
  # must say the safety check did not run rather than report a clean deletion.
  cat > "$TMP/shim33/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"worktree list"*-z*)
    if [[ -e "$TMP/shim33/deleted" ]]; then echo "fatal: fixture inventory failure" >&2; exit 128; fi ;;
  *"update-ref -d refs/heads/review/unreadable"*) : > "$TMP/shim33/deleted" ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim33/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim33:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "33. a failed post-deletion occupancy read is reported, not read as unoccupied"
  if (( RC == 2 )) && [[ "$OUT" == *"could not be read"* ]] && [[ "$OUT" == *"fixture inventory failure"* ]] && [[ "$(branches_deleted)" != *review/unreadable* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 32. a parent whose own name ends in a newline still confirms absence.
  mk_repo thirtytwo
  nl_parent="$ROOT/thirtytwo-p"$'\n'
  mkdir -p "$nl_parent" || die "mkdir newline parent failed"
  git -C "$SHARED" worktree add -q -b review/nlparent "$nl_parent/wt" origin/main 2>/dev/null \
    || die "fixture could not create a worktree under a newline-bearing parent"
  rm -rf "$nl_parent/wt" || die "rm failed"
  run "$SHARED"
  echo "32. absence is confirmed through a parent whose name ends in a newline"
  if (( RC == 0 )) && [[ "$ERRTEXT" != *"cannot confirm the worktree is gone"* ]] && ! has_branch "$SHARED" review/nlparent; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 34. the config cleanup will not delete a recreated branch's section.
  # The window is narrow: after the post-deletion occupancy re-check says the
  # branch is free, and before `--remove-section` runs. The shim recreates the
  # branch during the config READ, which sits exactly in it.
  mk_repo thirtyfour
  git -C "$SHARED" branch --no-track review/recreated origin/main || die "branch failed"
  git -C "$SHARED" config "branch.review/recreated.description" "original" || die "config failed"
  mkdir -p "$TMP/shim34" || die "mkdir shim failed"
  cat > "$TMP/shim34/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"config --get-regexp"*)
    if [[ ! -e "$TMP/shim34/seen" ]]; then
      : > "$TMP/shim34/seen"
      if ! "$(command -v git)" -C "$SHARED" worktree add -q --track -b review/recreated "$ROOT/thirtyfour-live" origin/main >/dev/null 2>&1; then
        echo "shim34: fixture could not recreate review/recreated" >&2
      fi
    fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim34/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ARCHIVE_IDLE_HOURS=100000 PATH="$TMP/shim34:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "34. a branch.<name> section recreated after the deletion is left untouched"
  kept_config=0
  config_rc=0
  git -C "$SHARED" config --get "branch.review/recreated.remote" >/dev/null || config_rc=$?
  case "$config_rc" in 0) kept_config=1 ;; 1) ;; *) die "git config --get failed (exit $config_rc)" ;; esac
  if (( kept_config )) && [[ "$OUT" == *"remove nothing by hand"* ]] && [[ "$OUT" == *'"branches_deleted": ['*'review/recreated'* ]]; then
    pass; else fail "the recreated branch's config must survive and be reported: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 35-38: one live run over idle worktrees of every kind.
  mk_repo idle
  local det="$ROOT/idle-detached" pushed="$ROOT/idle-pushed" busy="$ROOT/idle-busy" stale="$ROOT/idle-stale" held="$ROOT/idle-held"
  git -C "$SHARED" worktree add -q --detach "$det" origin/main 2>/dev/null || die "detached worktree add failed"
  add_wt "$SHARED" review/pushed "$pushed"; commit_in "$pushed" p.txt
  git -C "$pushed" push -q origin review/pushed 2>/dev/null || die "push review/pushed failed"
  git -C "$SHARED" fetch -q origin || die "fetch after push failed"
  git -C "$SHARED" worktree add -q --detach "$busy" origin/main 2>/dev/null || die "busy worktree add failed"
  add_wt "$SHARED" feat/stale "$stale"; commit_in "$stale" s.txt
  printf 'untracked work\n' > "$stale/notes.txt" || die "untracked write failed"
  git -C "$SHARED" worktree add -q --detach "$held" origin/main 2>/dev/null || die "held worktree add failed"
  git -C "$SHARED" worktree lock --reason "Active Herdr reviewer" "$held" || die "worktree lock failed"
  local stale_tip
  stale_tip="$(git -C "$stale" rev-parse HEAD)" || die "rev-parse stale HEAD failed"
  (cd "$busy" && exec sleep 300) &
  SLEEPER=$!
  local wt; for wt in "$det" "$pushed" "$busy" "$stale" "$held"; do age_wt "$wt"; done
  idle_run
  stop_sleeper
  echo "35. an idle clean worktree on a pushed commit is removed, detached or on a pushed branch"
  if (( RC == 0 )) && ! listed "$SHARED" "$det" && ! listed "$SHARED" "$pushed" \
    && [[ -n "$(removed_head "$det")" ]] && ! has_branch "$SHARED" review/pushed; then
    pass; else fail "reachable removal: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "36. a process with its cwd inside keeps an idle worktree"
  if [[ "$(kept_reason "$busy")" == in-use ]] && listed "$SHARED" "$busy"; then
    pass; else fail "in-use: out=$OUT"; fi
  echo "37. an idle unpushed worktree is archived with its commits and untracked files, then moved to the trash"
  local ref trash; ref="$(archived_ref "$stale")"; trash="$(trash_of "$stale")"
  if [[ "$ref" =~ ^refs/archive/worktrees/idle-stale-[0-9a-f]{10}-20200110T000000Z$ ]] && ! listed "$SHARED" "$stale" \
    && [[ "$trash" == "$ROOT/.trash/${ref#refs/archive/worktrees/}" ]] && listed "$SHARED" "$trash" \
    && git -C "$SHARED" merge-base --is-ancestor "$stale_tip" "$ref" \
    && [[ "$(git -C "$SHARED" show "$ref:notes.txt")" == "untracked work" ]] \
    && [[ "$(git -C "$SHARED" notes --ref=refs/notes/worktree-archive show "$ref" | python3 -c 'import json,sys; print(json.load(sys.stdin)["schema_version"])')" == 1 ]] \
    && has_branch "$SHARED" feat/stale \
    && git -C "$SHARED" worktree list --porcelain | grep -qxF "locked prune-worktrees archive $ref"; then
    pass; else fail "archive: ref=$ref trash=$trash out=$OUT err=$ERRTEXT"; fi
  idle_run
  echo "37b. a trash worktree is not judged again by the next run"
  if (( RC == 0 )) && ! mentions_path "$trash"; then
    pass; else fail "trash re-judged: out=$OUT"; fi
  echo "38. a locked idle worktree is kept with its lock reason"
  if [[ "$(kept_reason "$held")" == locked && "$(lock_reason_of "$held")" == "Active Herdr reviewer" ]] && listed "$SHARED" "$held"; then
    pass; else fail "locked: out=$OUT"; fi
  git -C "$SHARED" worktree unlock "$held" || die "worktree unlock failed"

  # --- 39-40: fresh activity, and idle past removal but short of archiving.
  mk_repo fresh
  local fresh_det="$ROOT/fresh-detached" mid="$ROOT/fresh-mid"
  git -C "$SHARED" worktree add -q --detach "$fresh_det" origin/main 2>/dev/null || die "fresh worktree add failed"
  add_wt "$SHARED" feat/mid "$mid"; commit_in "$mid" m.txt
  age_wt "$mid"
  idle_run PRUNE_ARCHIVE_IDLE_HOURS=100000
  echo "39. a reachable worktree with fresh activity is kept"
  if [[ "$(kept_reason "$fresh_det")" == detached ]] && listed "$SHARED" "$fresh_det"; then
    pass; else fail "not idle: out=$OUT"; fi
  echo "40. an unpushed worktree idle short of the archive window is kept"
  if [[ "$(kept_reason "$mid")" == unmerged ]] && listed "$SHARED" "$mid" \
    && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]]; then
    pass; else fail "below archive window: out=$OUT"; fi

  # --- 41. no process probe: nothing is judged idle.
  mk_repo noprobe
  local np="$ROOT/noprobe-detached"
  git -C "$SHARED" worktree add -q --detach "$np" origin/main 2>/dev/null || die "noprobe worktree add failed"
  age_wt "$np"
  idle_run PRUNE_LSOF="$TMP/no-such-lsof"
  echo "41. a missing process probe keeps an idle worktree as idle-unknown"
  if (( RC == 0 )) && [[ "$(kept_reason "$np")" == idle-unknown ]] && listed "$SHARED" "$np" && [[ "$ERRTEXT" == *"install lsof"* ]]; then
    pass; else fail "no probe: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 42. a failed snapshot keeps the worktree.
  mk_repo archfail
  local af="$ROOT/archfail-wt"
  add_wt "$SHARED" feat/archfail "$af"; commit_in "$af" a.txt
  age_wt "$af"
  mkdir -p "$TMP/shim42" || die "mkdir shim42 failed"
  local real_git; real_git="$(command -v git)" || die "git not found"
  # shellcheck disable=SC2016  # The shim's "$@" and $a must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nfor a in "$@"; do if [[ "$a" == commit-tree ]]; then echo "commit-tree refused" >&2; exit 1; fi; done\nexec %q "$@"\n' "$real_git" > "$TMP/shim42/git" \
    || die "shim42 write failed"
  chmod +x "$TMP/shim42/git" || die "chmod shim42 failed"
  idle_run PATH="$TMP/shim42:$PATH"
  echo "42. a failed archive keeps the worktree and reports the failure"
  if (( RC == 2 )) && listed "$SHARED" "$af" && [[ "$OUT" == *"archiving before removal failed"* ]] \
    && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]] && has_branch "$SHARED" feat/archfail; then
    pass; else fail "archive failure: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 43. dry run previews both new removals and changes nothing.
  mk_repo dryidle
  local dd="$ROOT/dryidle-detached" ds="$ROOT/dryidle-stale"
  git -C "$SHARED" worktree add -q --detach "$dd" origin/main 2>/dev/null || die "dryidle worktree add failed"
  add_wt "$SHARED" feat/drystale "$ds"; commit_in "$ds" d.txt
  age_wt "$dd"; age_wt "$ds"
  IDLE_ARGS=(--dry-run)
  idle_run
  IDLE_ARGS=()
  echo "43. a dry run previews the removal and the archive and changes nothing"
  if (( RC == 0 )) && [[ -n "$(removed_head "$dd")" && -n "$(archived_ref "$ds")" ]] \
    && listed "$SHARED" "$dd" && listed "$SHARED" "$ds" \
    && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]] && has_branch "$SHARED" feat/drystale; then
    pass; else fail "dry run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 44-45: the merged path waits for idleness and an empty worktree too.
  mk_repo mergedidle
  local mfresh="$ROOT/mergedidle-fresh" mbusy="$ROOT/mergedidle-busy"
  add_wt "$SHARED" review/mfresh "$mfresh"
  add_wt "$SHARED" review/mbusy "$mbusy"
  age_wt "$mbusy"
  (cd "$mbusy" && exec sleep 300) &
  SLEEPER=$!
  idle_run
  stop_sleeper
  echo "44. a clean merged worktree with fresh activity is kept"
  if [[ "$(kept_reason "$mfresh")" == merged-not-idle ]] && listed "$SHARED" "$mfresh" && has_branch "$SHARED" review/mfresh; then
    pass; else fail "merged not idle: out=$OUT"; fi
  echo "45. a process inside keeps a clean merged worktree"
  if [[ "$(kept_reason "$mbusy")" == in-use ]] && listed "$SHARED" "$mbusy"; then
    pass; else fail "merged in use: out=$OUT"; fi

  # --- 46. a tracked file edited now keeps an otherwise old worktree.
  mk_repo freshedit
  local fe="$ROOT/freshedit-wt"
  add_wt "$SHARED" feat/freshedit "$fe"; commit_in "$fe" e.txt
  age_wt "$fe"
  printf 'edited now\n' >> "$fe/e.txt" || die "edit failed"
  touch -t 202001091200 "$fe/e.txt" || die "touch edit failed"
  idle_run
  echo "46. a fresh edit to a tracked file keeps an old worktree from being archived"
  if [[ "$(kept_reason "$fe")" == dirty ]] && listed "$SHARED" "$fe" && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]]; then
    pass; else fail "fresh edit: out=$OUT err=$ERRTEXT"; fi

  # --- 47. a process arriving between the judgment and the removal keeps it.
  mk_repo race
  local rw="$ROOT/race-detached"
  git -C "$SHARED" worktree add -q --detach "$rw" origin/main 2>/dev/null || die "race worktree add failed"
  age_wt "$rw"
  lsof_turns_busy "$TMP/lsof47" 2 "$rw"
  idle_run PRUNE_LSOF="$TMP/lsof47/lsof"
  echo "47. a worktree that turns busy before its removal is kept"
  if (( RC == 0 )) && [[ "$(kept_reason "$rw")" == changed ]] && listed "$SHARED" "$rw" && [[ "$ERRTEXT" == *"process is now working inside"* ]]; then
    pass; else fail "changed at removal: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 48. a worktree that turns busy while it is archived keeps its archive and itself.
  mk_repo racearch
  local ra="$ROOT/racearch-wt"
  add_wt "$SHARED" feat/racearch "$ra"; commit_in "$ra" r.txt
  age_wt "$ra"
  lsof_turns_busy "$TMP/lsof48" 2 "$ra"
  idle_run PRUNE_LSOF="$TMP/lsof48/lsof"
  echo "48. a worktree that turns busy after its archive keeps the archive reported and the worktree"
  local ra_ref; ra_ref="$(archived_ref "$ra")"
  if [[ -n "$ra_ref" && -z "$(trash_of "$ra")" && "$(kept_reason "$ra")" == changed ]] && listed "$SHARED" "$ra" \
    && git -C "$SHARED" rev-parse --verify --quiet "$ra_ref" >/dev/null; then
    pass; else fail "changed after archive: out=$OUT err=$ERRTEXT"; fi

  # --- 49. a move that fails after the archive still reports the archive.
  mk_repo rmfail
  local rf="$ROOT/rmfail-wt"
  add_wt "$SHARED" feat/rmfail "$rf"; commit_in "$rf" f.txt
  age_wt "$rf"
  mkdir -p "$TMP/shim49" || die "mkdir shim49 failed"
  # shellcheck disable=SC2016  # The shim's "$@" and $a must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nfor a in "$@"; do if [[ "$a" == move ]]; then echo "move refused" >&2; exit 1; fi; done\nexec %q "$@"\n' "$real_git" > "$TMP/shim49/git" \
    || die "shim49 write failed"
  chmod +x "$TMP/shim49/git" || die "chmod shim49 failed"
  idle_run PATH="$TMP/shim49:$PATH"
  echo "49. a failed move after the archive still names the archive ref"
  if (( RC == 2 )) && [[ -n "$(archived_ref "$rf")" && -z "$(trash_of "$rf")" ]] && [[ "$OUT" == *"move refused"* ]] && listed "$SHARED" "$rf"; then
    pass; else fail "move failure: rc=$RC out=$OUT err=$ERRTEXT"; fi
  idle_run PATH="$TMP/shim49:$PATH"
  echo "49b. an archive still waiting on its trash is never stacked with another"
  if [[ "$(kept_reason "$rf")" == archive-pending ]] && [[ "$(git -C "$SHARED" for-each-ref refs/archive/ | wc -l | tr -d ' ')" == 1 ]]; then
    pass; else fail "pending archive: out=$OUT err=$ERRTEXT"; fi

  # --- 50a. a dry run previews an expiry, on its own repository.
  mk_repo expirydry
  local d1 d1ref d1trash
  d1="$(archive_one dexp1 feat/dexp1)"; d1ref="${d1%% *}"; d1trash="${d1#* }"
  age_trash "$d1trash"
  IDLE_ARGS=(--dry-run)
  later_run
  IDLE_ARGS=()
  echo "50a. a dry run previews the expiry and changes nothing"
  if (( RC == 0 )) && [[ "$(expired_refs)" == "$d1ref" ]] && listed "$SHARED" "$d1trash" \
    && git -C "$SHARED" rev-parse --verify --quiet "$d1ref" >/dev/null && has_branch "$SHARED" feat/dexp1; then
    pass; else fail "expiry dry run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 50b-c. expiry on a repository no dry run touched: every gate passes,
  #            then trash, branch, ref and note go.
  mk_repo expiry
  local e1 e1ref e1trash
  e1="$(archive_one exp1 feat/exp1)"; e1ref="${e1%% *}"; e1trash="${e1#* }"
  age_trash "$e1trash"
  local bare_c; bare_c="$(git -C "$SHARED" rev-parse origin/main)" || die "rev-parse origin/main failed"
  git -C "$SHARED" update-ref refs/archive/worktrees/nonote-0000000000-20191201T000000Z "$bare_c" || die "update-ref nonote failed"
  git -C "$SHARED" update-ref refs/archive/worktrees/nostamp "$bare_c" || die "update-ref nostamp failed"
  later_run
  echo "50b. a live run removes the trash worktree, its branch, the ref and the note"
  if (( RC == 0 )) && [[ "$(expired_refs)" == "$e1ref" ]] && ! listed "$SHARED" "$e1trash" && [[ ! -e "$e1trash" ]] \
    && ! has_branch "$SHARED" feat/exp1 && ! git -C "$SHARED" rev-parse --verify --quiet "$e1ref" >/dev/null; then
    pass; else fail "expiry: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "50c. a ref without a record is kept and reported, never expired; an unstamped ref is untouched"
  if [[ "$(archives_kept_reason refs/archive/worktrees/nonote-0000000000-20191201T000000Z)" == record-missing ]] \
    && git -C "$SHARED" rev-parse --verify --quiet refs/archive/worktrees/nonote-0000000000-20191201T000000Z >/dev/null \
    && git -C "$SHARED" rev-parse --verify --quiet refs/archive/worktrees/nostamp >/dev/null; then
    pass; else fail "no record: out=$OUT"; fi

  # --- 53-58: every gate that keeps an expiring archive.
  mk_repo gates
  local g_new g_bad g_chg g_lck g_busy other
  g_new="$(archive_one gnew feat/gnew)"; g_bad="$(archive_one gbad feat/gbad)"; g_chg="$(archive_one gchg feat/gchg)"
  g_lck="$(archive_one glck feat/glck)"; g_busy="$(archive_one gbusy feat/gbusy)"
  other="$ROOT/gates-other"
  git -C "$SHARED" worktree add -q --detach "$other" origin/main 2>/dev/null || die "other worktree add failed"
  local t; for t in "${g_new#* }" "${g_bad#* }" "${g_chg#* }" "${g_lck#* }" "${g_busy#* }"; do age_trash "$t"; done
  # 53: a newer schema.
  set_record "$SHARED" "${g_new%% *}" "$(record_of "$SHARED" "${g_new%% *}" | python3 -c 'import json,sys; r=json.load(sys.stdin); r["schema_version"]=2; print(json.dumps(r))')"
  # 54: a record pointing its trash at another registered worktree.
  set_record "$SHARED" "${g_bad%% *}" "$(record_of "$SHARED" "${g_bad%% *}" | python3 -c 'import json,sys; r=json.load(sys.stdin); r["trash"]=sys.argv[1]; print(json.dumps(r))' "$other")"
  # 55: content written into the trash after the archive.
  printf 'late\n' > "${g_chg#* }/late.txt" || die "late write failed"; age_trash "${g_chg#* }"
  # 56: locked.
  git -C "$SHARED" worktree unlock "${g_lck#* }" || die "unlock sweep's trash lock failed"
  git -C "$SHARED" worktree lock --reason "operator hold" "${g_lck#* }" || die "lock trash failed"
  # 57: a process inside.
  (cd "${g_busy#* }" && exec sleep 300) &
  SLEEPER=$!
  later_run
  stop_sleeper
  echo "53. a newer-schema record is kept and reported"
  if [[ "$(archives_kept_reason "${g_new%% *}")" == schema-newer ]] && listed "$SHARED" "${g_new#* }"; then pass; else fail "newer: out=$OUT"; fi
  echo "54. a record naming another worktree as its trash is kept, and that worktree untouched"
  if [[ "$(archives_kept_reason "${g_bad%% *}")" == record-invalid:* ]] && listed "$SHARED" "$other" && listed "$SHARED" "${g_bad#* }"; then pass; else fail "invalid: out=$OUT"; fi
  echo "55. a trash worktree holding content the archive lacks is kept"
  if [[ "$(archives_kept_reason "${g_chg%% *}")" == *"content the archive lacks"* ]] && [[ -e "${g_chg#* }/late.txt" ]]; then pass; else fail "changed trash: out=$OUT"; fi
  echo "56. a trash worktree locked by anyone but the sweep is kept"
  if [[ "$(archives_kept_reason "${g_lck%% *}")" == *"not locked by this sweep alone"* ]] && listed "$SHARED" "${g_lck#* }"; then pass; else fail "locked trash: out=$OUT"; fi
  echo "57. a trash worktree with a process inside is kept"
  if [[ "$(archives_kept_reason "${g_busy%% *}")" == *"process is working inside"* ]] && listed "$SHARED" "${g_busy#* }"; then pass; else fail "busy trash: out=$OUT"; fi
  git -C "$SHARED" worktree unlock "${g_lck#* }" || die "unlock trash failed"

  # --- 58. a missing trash worktree keeps its archive; the branch stays held.
  mk_repo moved
  local mv1; mv1="$(archive_one mv1 feat/mv1)"
  rm -rf "${mv1#* }" || die "rm trash dir failed"
  later_run
  echo "58. a trash worktree whose directory vanished keeps its archive, registration and branch"
  if (( RC == 0 )) && [[ "$(archives_kept_reason "${mv1%% *}")" == *"is missing"* ]] \
    && git -C "$SHARED" rev-parse --verify --quiet "${mv1%% *}" >/dev/null \
    && listed "$SHARED" "${mv1#* }" && has_branch "$SHARED" feat/mv1 && ! mentions_path "${mv1#* }"; then
    pass; else fail "missing trash: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 59. a path lsof cannot report faithfully is never judged idle.
  mk_repo newline
  local nlwt="$ROOT/nl"$'\n'"wt" nlbusy="$ROOT/nlb"$'\n'"busy"
  git -C "$SHARED" worktree add -q --detach "$nlwt" origin/main 2>/dev/null || die "newline worktree add failed"
  git -C "$SHARED" worktree add -q --detach "$nlbusy" origin/main 2>/dev/null || die "newline busy worktree add failed"
  age_wt "$nlwt"; age_wt "$nlbusy"
  (cd "$nlbusy" && exec sleep 300) &
  SLEEPER=$!
  idle_run
  stop_sleeper
  echo "59. worktrees whose path holds a newline are kept as idle-unknown, a process inside or not"
  if (( RC == 0 )) && [[ "$(kept_reason "$nlwt")" == idle-unknown && "$(kept_reason "$nlbusy")" == idle-unknown ]] \
    && listed "$SHARED" "$nlwt" && listed "$SHARED" "$nlbusy"; then
    pass; else fail "newline: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 51. a dirty submodule keeps an idle worktree; it is never archived.
  mk_repo submod
  local sub_origin="$TMP/sublib.git" sw="$ROOT/submod-wt"
  git init -q --bare -b main "$sub_origin" || die "sub origin init failed"
  git clone -q "$sub_origin" "$TMP/sublib-seed" 2>/dev/null || die "sub seed clone failed"
  printf 's\n' > "$TMP/sublib-seed/s" || die "sub seed write failed"
  git -C "$TMP/sublib-seed" -c user.name=t -c user.email=t@t add s || die "sub add failed"
  git -C "$TMP/sublib-seed" -c user.name=t -c user.email=t@t commit -q -m s1 || die "sub commit failed"
  git -C "$TMP/sublib-seed" push -q origin main 2>/dev/null || die "sub push failed"
  add_wt "$SHARED" feat/submod "$sw"
  git -C "$sw" -c protocol.file.allow=always submodule --quiet add "$sub_origin" lib 2>/dev/null || die "submodule add failed"
  git -C "$sw" -c user.name=t -c user.email=t@t commit -q -m "add lib" || die "submodule commit failed"
  printf 'nested edit\n' >> "$sw/lib/s" || die "nested edit failed"
  age_wt "$sw"
  idle_run
  echo "51. a worktree whose submodule holds changes is kept, never archived"
  if [[ "$(kept_reason "$sw")" == submodule-dirty ]] && listed "$SHARED" "$sw" && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]]; then
    pass; else fail "submodule: out=$OUT err=$ERRTEXT"; fi

  # --- 52. two worktrees sharing a basename get two archives.
  mk_repo samename
  local s1="$ROOT/a/wt" s2="$ROOT/b/wt"
  mkdir -p "$ROOT/a" "$ROOT/b" || die "mkdir samename failed"
  add_wt "$SHARED" feat/same1 "$s1"; commit_in "$s1" one.txt
  add_wt "$SHARED" feat/same2 "$s2"; commit_in "$s2" two.txt
  age_wt "$s1"; age_wt "$s2"
  idle_run
  echo "52. two worktrees with one basename are archived to distinct refs"
  local ref1 ref2; ref1="$(archived_ref "$s1")"; ref2="$(archived_ref "$s2")"
  if (( RC == 0 )) && [[ -n "$ref1" && -n "$ref2" && "$ref1" != "$ref2" ]] && ! listed "$SHARED" "$s1" && ! listed "$SHARED" "$s2"; then
    pass; else fail "same basename: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 60. only origin's refs prove reachability.
  mk_repo otherremote
  local orw="$ROOT/otherremote-wt"
  git init -q --bare -b main "$TMP/upstream.git" || die "upstream init failed"
  git -C "$SHARED" remote add upstream "$TMP/upstream.git" || die "remote add failed"
  git -C "$SHARED" worktree add -q --detach "$orw" origin/main 2>/dev/null || die "otherremote worktree add failed"
  commit_in "$orw" u.txt
  git -C "$orw" push -q upstream HEAD:refs/heads/side 2>/dev/null || die "push upstream failed"
  git -C "$SHARED" fetch -q upstream || die "fetch upstream failed"
  age_wt "$orw"
  idle_run PRUNE_ARCHIVE_IDLE_HOURS=100000
  echo "60. a HEAD held only by another remote's ref is not reachable"
  if [[ "$(kept_reason "$orw")" == detached ]] && listed "$SHARED" "$orw"; then
    pass; else fail "other remote: out=$OUT err=$ERRTEXT"; fi

  # --- 61. the reachability proof is re-derived just before the removal.
  mk_repo proofgone
  local pg="$ROOT/proofgone-wt"
  git -C "$SHARED" worktree add -q --detach "$pg" origin/main 2>/dev/null || die "proofgone worktree add failed"
  age_wt "$pg"
  mkdir -p "$TMP/shim61" || die "mkdir shim61 failed"
  # The second read of origin's branch tips (`ls-remote --heads`, the one just
  # before the removal) answers that origin holds no branch at all.
  # shellcheck disable=SC2016  # The shim's "$@" and $a must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nfor a in "$@"; do if [[ "$a" == --heads ]]; then printf x >> %q; if [[ "$(cat %q)" == xx ]]; then exit 0; fi; fi; done\nexec %q "$@"\n' \
    "$TMP/shim61/n" "$TMP/shim61/n" "$real_git" > "$TMP/shim61/git" || die "shim61 write failed"
  chmod +x "$TMP/shim61/git" || die "chmod shim61 failed"
  idle_run PATH="$TMP/shim61:$PATH"
  echo "61. a worktree whose origin ref vanished before its removal is kept"
  if [[ "$(kept_reason "$pg")" == changed ]] && listed "$SHARED" "$pg" && [[ "$ERRTEXT" == *"origin no longer holds"* ]]; then
    pass; else fail "proof gone: out=$OUT err=$ERRTEXT"; fi

  # --- 62. a dirty gitlink is found without .gitmodules; an embedded repository keeps a worktree.
  mk_repo gitlink
  local gl="$ROOT/gitlink-wt" nr="$ROOT/gitlink-nested"
  add_wt "$SHARED" feat/gitlink "$gl"
  git -C "$gl" -c protocol.file.allow=always submodule --quiet add "$sub_origin" lib 2>/dev/null || die "submodule add failed"
  git -C "$gl" -c user.name=t -c user.email=t@t commit -q -m "add lib" || die "submodule commit failed"
  rm "$gl/.gitmodules" || die "rm .gitmodules failed"
  printf 'nested edit\n' >> "$gl/lib/s" || die "nested edit failed"
  add_wt "$SHARED" feat/nested "$nr"; commit_in "$nr" base.txt
  git init -q "$nr/embedded" || die "embedded init failed"
  printf 'inner\n' > "$nr/embedded/inner.txt" || die "inner write failed"
  # An embedded repository inside an ignored directory, and one nested below an
  # untracked directory: git's own listing shows neither.
  local ig="$ROOT/gitlink-ignored" deep="$ROOT/gitlink-deep"
  add_wt "$SHARED" feat/ignored "$ig"
  printf 'vendor/\n' > "$ig/.gitignore" || die "gitignore write failed"
  git -C "$ig" -c user.name=t -c user.email=t@t add .gitignore || die "gitignore add failed"
  git -C "$ig" -c user.name=t -c user.email=t@t commit -q -m ignore || die "gitignore commit failed"
  mkdir -p "$ig/vendor" || die "mkdir vendor failed"
  git init -q "$ig/vendor/lib" || die "ignored embedded init failed"
  add_wt "$SHARED" feat/deep "$deep"; commit_in "$deep" d.txt
  mkdir -p "$deep/outer" || die "mkdir outer failed"
  printf 'o\n' > "$deep/outer/plain.txt" || die "outer write failed"
  git init -q "$deep/outer/inner" || die "deep embedded init failed"
  age_wt "$gl"; age_wt "$nr"; age_wt "$ig"; age_wt "$deep"
  idle_run
  echo "62a. a dirty submodule is kept even with .gitmodules gone"
  if [[ "$(kept_reason "$gl")" == submodule-dirty ]] && listed "$SHARED" "$gl"; then pass; else fail "gitlink: out=$OUT err=$ERRTEXT"; fi
  echo "62b. an untracked embedded repository keeps its worktree"
  if [[ "$(kept_reason "$nr")" == nested-repo ]] && listed "$SHARED" "$nr"; then
    pass; else fail "nested repo: out=$OUT err=$ERRTEXT"; fi
  echo "62c. an embedded repository in an ignored directory, or below an untracked one, keeps its worktree"
  if [[ "$(kept_reason "$ig")" == nested-repo && "$(kept_reason "$deep")" == nested-repo ]] && listed "$SHARED" "$ig" && listed "$SHARED" "$deep" \
    && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]]; then
    pass; else fail "hidden nested repos: out=$OUT err=$ERRTEXT"; fi

  # --- 63. an older record with no migration, and an unparseable one, are kept.
  mk_repo oldrec
  local o1 o2
  o1="$(archive_one oldv0 feat/oldv0)"; o2="$(archive_one garbled feat/garbled)"
  age_trash "${o1#* }"; age_trash "${o2#* }"
  set_record "$SHARED" "${o1%% *}" "$(record_of "$SHARED" "${o1%% *}" | python3 -c 'import json,sys; r=json.load(sys.stdin); r["schema_version"]=0; print(json.dumps(r))')"
  set_record "$SHARED" "${o2%% *}" "not json"
  later_run
  # Versions start at 1: a 0 is malformed, not old. The unmigratable path
  # needs a version between 1 and ARCHIVE_SCHEMA, which v1 does not have yet.
  echo "63. a record with schema_version 0, or unparseable, is kept and never expired"
  if [[ "$(archives_kept_reason "${o1%% *}")" == record-unparseable && "$(archives_kept_reason "${o2%% *}")" == record-unparseable ]] \
    && listed "$SHARED" "${o1#* }" && listed "$SHARED" "${o2#* }"; then
    pass; else fail "old records: out=$OUT"; fi

  # --- 64. two archives of one parent and tree in one second stay two records.
  mk_repo twins
  local twin_c tw1="$ROOT/twin-a/wt" tw2="$ROOT/twin-b/wt"
  twin_c="$(git -C "$SHARED" -c user.name=t -c user.email=t@t commit-tree "origin/main^{tree}" -p origin/main -m local)" || die "commit-tree failed"
  mkdir -p "$ROOT/twin-a" "$ROOT/twin-b" || die "mkdir twins failed"
  git -C "$SHARED" worktree add -q --detach "$tw1" "$twin_c" 2>/dev/null || die "twin a add failed"
  git -C "$SHARED" worktree add -q --detach "$tw2" "$twin_c" 2>/dev/null || die "twin b add failed"
  age_wt "$tw1"; age_wt "$tw2"
  idle_run
  echo "64. two same-parent, same-tree archives in one second get two commits and two records"
  local tr1 tr2 tc1 tc2
  tr1="$(archived_ref "$tw1")"; tr2="$(archived_ref "$tw2")"
  if (( RC == 0 )) && [[ -n "$tr1" && -n "$tr2" ]] \
    && tc1="$(git -C "$SHARED" rev-parse "$tr1")" && tc2="$(git -C "$SHARED" rev-parse "$tr2")" && [[ "$tc1" != "$tc2" ]] \
    && [[ "$(record_of "$SHARED" "$tr1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ref"])')" == "$tr1" ]] \
    && [[ "$(record_of "$SHARED" "$tr2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ref"])')" == "$tr2" ]]; then
    pass; else fail "twins: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 65. a ref with a slash in its name part is never this script's.
  mk_repo slashref
  local sl sl_c
  sl="$(archive_one slash1 feat/slash1)"
  sl_c="$(git -C "$SHARED" rev-parse "${sl%% *}")" || die "rev-parse slash archive failed"
  git -C "$SHARED" update-ref "refs/archive/worktrees/x/y-0123456789-20191201T000000Z" "$sl_c" || die "update-ref slash failed"
  later_run
  echo "65. a ref outside the generated name charset is not planned or reported"
  if (( RC == 0 )) && [[ "$OUT" != *"x/y-0123456789"* ]] && git -C "$SHARED" rev-parse --verify --quiet "refs/archive/worktrees/x/y-0123456789-20191201T000000Z" >/dev/null; then
    pass; else fail "slash ref: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 66. a process arriving after the expiry gates keeps the trash.
  mk_repo latebusy
  local lb; lb="$(archive_one late1 feat/late1)"
  age_trash "${lb#* }"
  lsof_turns_busy "$TMP/lsof66" 2 "${lb#* }"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$LATER_NOW" PRUNE_LSOF="$TMP/lsof66/lsof" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "66. a process that enters the trash after its gates, before the forced removal, keeps it"
  if (( RC == 0 )) && [[ "$(archives_kept_reason "${lb%% *}")" == *"process entered"* ]] && listed "$SHARED" "${lb#* }" \
    && git -C "$SHARED" rev-parse --verify --quiet "${lb%% *}" >/dev/null; then
    pass; else fail "late busy: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 67. a boolean schema_version is unusable, never version 1.
  mk_repo boolschema
  local bs; bs="$(archive_one bool1 feat/bool1)"
  age_trash "${bs#* }"
  set_record "$SHARED" "${bs%% *}" "$(record_of "$SHARED" "${bs%% *}" | python3 -c 'import json,sys; r=json.load(sys.stdin); r["schema_version"]=True; print(json.dumps(r))')"
  later_run
  echo "67. a record whose schema_version is true is unparseable and kept"
  if [[ "$(archives_kept_reason "${bs%% *}")" == record-unparseable ]] && listed "$SHARED" "${bs#* }"; then pass; else fail "bool schema: out=$OUT"; fi

  # --- 68. an expiry interrupted between the ref and the note leaves an orphan
  #         note, which the next live run removes.
  mk_repo notefail
  local nf nf_c; nf="$(archive_one note1 feat/note1)"
  nf_c="$(git -C "$SHARED" rev-parse "${nf%% *}")" || die "rev-parse archive failed"
  age_trash "${nf#* }"
  mkdir -p "$TMP/shim68" || die "mkdir shim68 failed"
  # shellcheck disable=SC2016  # The shim's "$@" and $a must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nfor a in "$@"; do if [[ "$a" == remove ]]; then for b in "$@"; do if [[ "$b" == notes ]]; then echo "notes remove refused" >&2; exit 1; fi; done; fi; done\nexec %q "$@"\n' "$real_git" > "$TMP/shim68/git" \
    || die "shim68 write failed"
  chmod +x "$TMP/shim68/git" || die "chmod shim68 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$LATER_NOW" PATH="$TMP/shim68:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "68a. a note removal that fails after the ref is gone reports a failure, not an expiry"
  if (( RC == 2 )) && [[ "$(expired_refs)" == "" ]] && [[ "$OUT" == *"notes remove refused"* ]] \
    && ! git -C "$SHARED" rev-parse --verify --quiet "${nf%% *}" >/dev/null \
    && git -C "$SHARED" notes --ref=refs/notes/worktree-archive show "$nf_c" >/dev/null; then
    pass; else fail "interrupted expiry: rc=$RC out=$OUT err=$ERRTEXT"; fi
  later_run
  echo "68b. the next live run removes the orphan note"
  if (( RC == 0 )) && [[ "$(python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["orphan_notes_removed"]))' <<<"$OUT")" == "$nf_c" ]] \
    && ! has_note "$SHARED" "$nf_c"; then
    pass; else fail "orphan note: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 69. dry run and live run agree after origin deletes a branch.
  mk_repo agree
  local ag="$ROOT/agree-wt"
  git -C "$SHARED" worktree add -q --detach "$ag" origin/main 2>/dev/null || die "agree worktree add failed"
  commit_in "$ag" gone.txt
  git -C "$ag" push -q origin HEAD:refs/heads/side 2>/dev/null || die "push side failed"
  git -C "$SHARED" fetch -q origin || die "fetch side failed"
  git -C "$SEED" push -q origin --delete side 2>/dev/null || die "delete side on origin failed"
  age_wt "$ag"
  IDLE_ARGS=(--dry-run)
  idle_run PRUNE_ARCHIVE_IDLE_HOURS=100000
  IDLE_ARGS=()
  local dry_reason; dry_reason="$(kept_reason "$ag")"
  idle_run PRUNE_ARCHIVE_IDLE_HOURS=100000
  echo "69. a HEAD held only by an origin branch deleted since the last fetch is kept by both dry and live runs"
  if [[ "$dry_reason" == detached && "$(kept_reason "$ag")" == detached ]] && listed "$SHARED" "$ag"; then
    pass; else fail "dry/live agreement: dry=$dry_reason out=$OUT err=$ERRTEXT"; fi

  # --- 70. a symlinked .trash keeps the worktree; nothing is archived.
  local root70="$TMP/root70"
  mkdir -p "$root70" "$TMP/elsewhere70" || die "mkdir root70 failed"
  ln -s "$TMP/elsewhere70" "$root70/.trash" || die "symlink .trash failed"
  mk_repo symtrash
  local st="$root70/symtrash-wt"
  add_wt "$SHARED" feat/symtrash "$st"; commit_in "$st" s.txt
  age_wt "$st"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$root70" PRUNE_NOW="$IDLE_NOW" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "70. a symlinked .trash keeps an archive candidate and writes no archive"
  if (( RC == 0 )) && [[ "$(kept_reason "$st")" == trash-unsafe ]] && listed "$SHARED" "$st" \
    && [[ -z "$(git -C "$SHARED" for-each-ref refs/archive/)" ]] && [[ -z "$(ls -A "$TMP/elsewhere70")" ]]; then
    pass; else fail "symlinked trash: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 71. a force-push that drops the merge between judgment and removal keeps it.
  mk_repo forcepush
  local fp="$ROOT/forcepush-wt" fp_base
  fp_base="$(git -C "$SHARED" rev-parse origin/main)" || die "rev-parse base failed"
  add_wt "$SHARED" review/fp "$fp"; commit_in "$fp" fp.txt
  git -C "$fp" push -q origin HEAD:main 2>/dev/null || die "push to main failed"
  git -C "$SHARED" fetch -q origin || die "fetch after merge failed"
  age_wt "$fp"
  mkdir -p "$TMP/shim71" || die "mkdir shim71 failed"
  # The second read of origin's main (just before the removal) answers with the
  # pre-merge commit, as after a force-push that dropped the merge.
  # shellcheck disable=SC2016  # The shim's "$@" and $a must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *ls-remote*refs/heads/main* ]]; then printf x >> %q; if [[ "$(cat %q)" == xx ]]; then printf "%%s\\trefs/heads/main\\n" %q; exit 0; fi; fi\nexec %q "$@"\n' \
    "$TMP/shim71/n" "$TMP/shim71/n" "$fp_base" "$real_git" > "$TMP/shim71/git" || die "shim71 write failed"
  chmod +x "$TMP/shim71/git" || die "chmod shim71 failed"
  idle_run PATH="$TMP/shim71:$PATH"
  echo "71. a merge dropped by a force-push before the removal keeps the worktree and its branch"
  if [[ "$(kept_reason "$fp")" == changed ]] && listed "$SHARED" "$fp" && has_branch "$SHARED" review/fp \
    && [[ "$ERRTEXT" == *"origin no longer holds"* ]]; then
    pass; else fail "force-push: out=$OUT err=$ERRTEXT"; fi

  # --- 72. a failing remote command never relays the remote URL.
  mk_repo secretremote
  # git strips user:password@ from its own errors but prints the rest of the
  # URL, so the token sits in the path, where it survives into stderr.
  git -C "$SHARED" remote set-url origin "http://127.0.0.1:9/s3cr3t-token/repo.git" || die "set-url failed"
  run "$SHARED"
  echo "72. a failed fetch reports its exit code and repair, never the credential-bearing URL"
  if (( RC == 1 )) && [[ "$ERRTEXT" == *"exited"* && "$ERRTEXT" == *"to see why"* ]] && [[ "$ERRTEXT" != *s3cr3t-token* ]]; then
    pass; else fail "secret redaction: rc=$RC err=$ERRTEXT"; fi

  # --- 73. a checkout to another commit with the same tree, after the expiry
  #         gates and before the forced removal, keeps the archive.
  mk_repo samedtree
  local sd sd_trash sd_other; sd="$(archive_one same1 feat/same1)"; sd_trash="${sd#* }"
  sd_other="$(git -C "$sd_trash" -c user.name=t -c user.email=t@t commit-tree "HEAD^{tree}" -p HEAD -m sametree)" || die "commit-tree failed"
  age_trash "$sd_trash"
  mkdir -p "$TMP/lsof73" || die "mkdir lsof73 failed"
  # The second probe (the pre-removal one) first switches the trash worktree to a
  # commit with the same tree, then reports no process.
  # shellcheck disable=SC2016  # The stand-in's $(...) must run in the stand-in, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf x >> %q\nif [[ "$(cat %q)" == xx ]]; then %q -C %q checkout -q --detach %q; fi\n' \
    "$TMP/lsof73/calls" "$TMP/lsof73/calls" "$real_git" "$sd_trash" "$sd_other" > "$TMP/lsof73/lsof" || die "write lsof73 failed"
  chmod +x "$TMP/lsof73/lsof" || die "chmod lsof73 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$LATER_NOW" PRUNE_LSOF="$TMP/lsof73/lsof" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "73. a same-tree checkout just before the forced removal keeps the archive and the trash"
  if (( RC == 0 )) && [[ "$(archives_kept_reason "${sd%% *}")" == *"HEAD, branch or lock changed"* ]] && listed "$SHARED" "$sd_trash" \
    && git -C "$SHARED" rev-parse --verify --quiet "${sd%% *}" >/dev/null; then
    pass; else fail "same-tree checkout: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 74. an ignored file that changes in the trash keeps the archive.
  mk_repo ignoredenv
  local ie="$ROOT/ignoredenv-wt"
  add_wt "$SHARED" feat/ienv "$ie"
  printf '.env\n' > "$ie/.gitignore" || die "gitignore write failed"
  git -C "$ie" -c user.name=t -c user.email=t@t add .gitignore || die "gitignore add failed"
  git -C "$ie" -c user.name=t -c user.email=t@t commit -q -m ignore || die "gitignore commit failed"
  printf 'TOKEN=old\n' > "$ie/.env" || die "env write failed"
  age_wt "$ie"
  idle_run
  local ie_ref ie_trash; ie_ref="$(archived_ref "$ie")"; ie_trash="$(trash_of "$ie")"
  [[ -n "$ie_trash" ]] || die "74 setup: not archived, out=$OUT err=$ERRTEXT"
  printf 'TOKEN=new, longer\n' > "$ie_trash/.env" || die "env rewrite failed"
  age_trash "$ie_trash"
  later_run
  echo "74. an ignored .env changed in the trash after archival keeps the archive and the trash"
  if [[ "$(archives_kept_reason "$ie_ref")" == *"ignored files changed"* ]] && listed "$SHARED" "$ie_trash" && [[ -e "$ie_trash/.env" ]]; then
    pass; else fail "ignored inventory: out=$OUT err=$ERRTEXT"; fi

  # --- 75. an embedded repository created in the trash keeps the archive.
  mk_repo trashnest
  local tn tn_trash; tn="$(archive_one tnest feat/tnest)"; tn_trash="${tn#* }"
  git init -q "$tn_trash/inner" || die "trash embedded init failed"
  age_trash "$tn_trash"
  later_run
  echo "75. an embedded repository created in the trash keeps the archive"
  if [[ "$(archives_kept_reason "${tn%% *}")" == *"another repository's checkout (nested-repo)"* ]] && [[ -d "$tn_trash/inner/.git" ]]; then
    pass; else fail "trash nested repo: out=$OUT err=$ERRTEXT"; fi

  # --- 76. .trash swapped for a symlink during the move: moved back, archive kept.
  mk_repo trashswap
  local ts="$ROOT/trashswap-wt" swap_dir="$TMP/swapped-elsewhere"
  add_wt "$SHARED" feat/tswap "$ts"; commit_in "$ts" ts.txt
  age_wt "$ts"
  rm -rf "$ROOT/.trash" || die "clear .trash failed"
  mkdir -p "$ROOT/.trash" "$swap_dir" || die "mkdir trash/swap failed"
  mkdir -p "$TMP/shim76" || die "mkdir shim76 failed"
  # shellcheck disable=SC2016  # The shim's "$@" must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *"worktree move"* && ! -L %q ]]; then mv %q %q; ln -s %q %q; fi\nexec %q "$@"\n' \
    "$ROOT/.trash" "$ROOT/.trash" "$TMP/trash-aside" "$swap_dir" "$ROOT/.trash" "$real_git" > "$TMP/shim76/git" || die "shim76 write failed"
  chmod +x "$TMP/shim76/git" || die "chmod shim76 failed"
  idle_run PATH="$TMP/shim76:$PATH"
  echo "76. a .trash swapped for a symlink mid-move: the worktree comes back, its archive is kept"
  if (( RC == 2 )) && listed "$SHARED" "$ts" && [[ -e "$ts/ts.txt" ]] && [[ -n "$(archived_ref "$ts")" && -z "$(trash_of "$ts")" ]] \
    && [[ "$OUT" == *"was moved back"* ]] && [[ -z "$(ls -A "$swap_dir")" ]]; then
    pass; else fail "trash swap: rc=$RC out=$OUT err=$ERRTEXT"; fi
  rm "$ROOT/.trash" || die "remove swapped symlink failed"
  mv "$TMP/trash-aside" "$ROOT/.trash" || die "restore .trash failed"

  # --- 77. a trash worktree whose .git is a symlink keeps the archive.
  mk_repo trashlink
  local tl tl_trash; tl="$(archive_one tlink feat/tlink)"; tl_trash="${tl#* }"
  cp "$tl_trash/.git" "$TMP/tl-dotgit" || die "copy .git failed"
  rm "$tl_trash/.git" || die "rm .git failed"
  ln -s "$TMP/tl-dotgit" "$tl_trash/.git" || die "symlink .git failed"
  age_trash "$tl_trash"
  later_run
  echo "77. a trash worktree whose .git is a symlink keeps the archive"
  if [[ "$(archives_kept_reason "${tl%% *}")" == *".git is a symlink"* ]] && git -C "$SHARED" rev-parse --verify --quiet "${tl%% *}" >/dev/null; then
    pass; else fail "symlinked .git: out=$OUT err=$ERRTEXT"; fi

  # --- 14. usage / not a repo.
  run
  echo "14a. usage is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *usage* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  mkdir -p "$TMP/notrepo" || die "mkdir failed"
  run "$TMP/notrepo"
  echo "14b. a non-repository is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  run "$SHARED" --bogus
  echo "14c. an unknown flag is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  echo
  echo "passed=$PASS failed=$FAIL"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
