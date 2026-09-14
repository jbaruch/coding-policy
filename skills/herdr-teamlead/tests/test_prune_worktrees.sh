#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/prune-worktrees.sh.
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
#   6. Detached            -> kept, reason detached.
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
#  17. Dry-run metadata    -> stale worktree metadata survives a dry run.
#
# Run: bash skills/herdr-teamlead/tests/test_prune_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
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
  OUT="$(env WORKTREE_ROOT="$ROOT" bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
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
  grep -qx "worktree $2" <<<"$inventory" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "grep failed (exit $rc) reading the worktree inventory" ;; esac
}

main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/prune-worktrees.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  # The script reports physical paths; macOS mktemp hands out a symlinked /var.
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT
  ROOT="$TMP/worktrees"
  mkdir -p "$ROOT" || die "mkdir root failed"

  # --- 1, 3, 4, 5, 6, 7, 9, 10 share one repo: one run decides them all.
  mk_repo one
  add_wt "$SHARED" review/merged "$ROOT/one-merged"
  add_wt "$SHARED" test/unmerged "$ROOT/one-unmerged"; commit_in "$ROOT/one-unmerged" u
  add_wt "$SHARED" test/untracked "$ROOT/one-untracked"; printf 'z\n' > "$ROOT/one-untracked/scratch" || die "write failed"
  add_wt "$SHARED" test/modified "$ROOT/one-modified"; printf 'changed\n' > "$ROOT/one-modified/f" || die "write failed"
  git -C "$SHARED" worktree add -q --detach "$ROOT/one-detached" origin/main 2>/dev/null || die "detached add failed"
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
  echo "4. untracked file keeps the worktree as dirty"
  if [[ "$(kept_reason "$ROOT/one-untracked")" == dirty ]] && [[ -f "$ROOT/one-untracked/scratch" ]]; then pass; else fail "out=$OUT"; fi
  echo "5. modified file keeps the worktree as dirty"
  if [[ "$(kept_reason "$ROOT/one-modified")" == dirty ]] && [[ -d "$ROOT/one-modified" ]]; then pass; else fail "out=$OUT"; fi
  echo "6. detached worktree is kept"
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
  echo "12. a hand-deleted worktree's metadata is pruned"
  if (( RC == 0 )) && ! listed "$SHARED" "$ROOT/twelve-gone"; then pass; else fail "rc=$RC out=$OUT"; fi

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
  # origin's default is now `vanished`; a git shim corrupts its remote-tracking
  # ref the moment the script reaches merge-base, after the run's own fetch.
  mkdir -p "$TMP/shim" || die "mkdir shim failed"
  cat > "$TMP/shim/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
case "\$*" in *merge-base*) printf 'not-a-sha\n' > "$SHARED/.git/refs/remotes/origin/vanished" ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim/git" || die "chmod shim failed"
  run_with_shim() { RUN_SEQ=$((RUN_SEQ+1)); OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"; }
  run_with_shim
  echo "16. a merge-base tool failure lands in failed, on stderr, exit 2, worktree untouched"
  if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*merge-base* ]] && [[ "$ERRTEXT" == *"merge-base failed"* ]] && [[ "$(kept_reason "$ROOT/sixteen-broken")" == "" ]] && [[ -d "$ROOT/sixteen-broken" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 17. dry run leaves stale metadata and remote-tracking refs in place.
  mk_repo seventeen
  add_wt "$SHARED" review/preview "$ROOT/seventeen-preview"
  rm -rf "$ROOT/seventeen-preview" || die "rm failed"
  run "$SHARED" --dry-run
  echo "17. dry run does not prune stale metadata"
  if (( RC == 0 )) && listed "$SHARED" "$ROOT/seventeen-preview"; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

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
