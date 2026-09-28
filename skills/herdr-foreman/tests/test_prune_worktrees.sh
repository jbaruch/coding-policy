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
#   3. Unpushed, idle      -> kept, reason unpushed; branch survives.
#   4. Dirty (untracked)   -> kept, reason dirty.
#   5. Dirty (modified)    -> kept, reason dirty.
#   6. Detached, unpushed  -> kept, reason unpushed.
#   7. Locked              -> kept, reason locked.
#   8. Outside the root    -> kept, reason outside-root, never removed.
#   9. Shared checkout     -> never listed, never removed; default branch kept.
#  10. Branch, no worktree -> merged one deleted, unpushed one kept.
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
#  23. Deferred prunable   -> a prunable branch survives a skipped metadata removal.
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
#  87. Root replaced      -> a root swapped after the first removal stops
#                             every later removal, branch deletion and the
#                             metadata removal; exit 2.
#  88. Caller's root id    -> a root other than PRUNE_ROOT_ID names decides
#                             nothing; exit 1.
#  89. Root unlistable     -> a root that loses its permissions mid-run is a
#                             changed root (skipped as root, who lists
#                             anything).
#  90. Root moved at the   -> a root renamed between its last identity check
#      metadata step          and the metadata removal keeps the registrations
#                             of the worktrees it still holds.
#  91. Metadata removal    -> a failed per-entry removal leaves the entry
#      fails                  registered and its branch undeleted; exit 2
#                             (skipped as root).
#  92. Path reappears      -> a worktree moved back between the absence check
#                             and the cleanup keeps its files, commits and
#                             registration; exit 2.
#  93. Stale outside root  -> a vanished registration outside the root is
#                             dropped too.
#  94. Relative link       -> a vanished registration git recorded with a
#                             relative gitdir is dropped (skipped on a git
#                             without --relative-paths).
#  34. Recreated config   -> a branch.<name> section recreated after the
#                             deletion is left untouched.
#  35. Reachable, idle      -> a clean worktree whose HEAD an origin branch
#                             holds is removed, detached or on a pushed branch.
#  36. In use               -> a process with its cwd inside keeps it.
#  37. Dirty, idle          -> kept and reported with its changed-file count and
#                             command; nothing touched.
#  38. Locked, idle         -> kept, reported with its lock reason.
#  39. Not idle             -> fresh activity keeps a reachable worktree.
#  40. Unpushed, idle       -> kept and reported with its commit count and push
#                             command.
#  41. No process probe     -> lsof missing keeps it as idle-unknown.
#  43. Dry run              -> the removal is previewed, nothing changes.
#  44-45. Merged path       -> fresh activity or a process inside keeps a clean
#                             merged worktree.
#  46. Fresh edit           -> a tracked file edited now keeps an old worktree.
#  47. Changed at removal   -> a process arriving before the removal keeps it,
#                             in a dry run's preview too (47b).
#  51. Dirty submodule      -> kept.
#  59. Newline path         -> lsof cannot report it faithfully: never idle.
#  60. Other remote         -> a ref of a remote other than origin proves nothing.
#  61. Proof gone           -> reachability re-derived just before removal.
#  62. Gitlink, embedded    -> a dirty gitlink without .gitmodules, and an
#                             embedded repository (untracked, ignored, or below
#                             an untracked directory), keep the worktree.
#  69. Dry/live agreement   -> both judge origin as it is now.
#  71. Force-push           -> a merge dropped before the removal keeps it.
#  72. Credential URL       -> a failed remote command never relays the URL.
#  80. Origin-held branch   -> a branch with no worktree whose tip origin holds
#                             is deleted.
#  81. Unpushed branch      -> idle: reported with count, age and command;
#                             fresh: not-idle; neither is deleted.
#  82. Branch proof         -> a merge dropped before the deletion keeps it.
#  83. Partial lsof         -> an unreadable cwd of a live process: idle-unknown;
#                             a mount lsof could not stat, or an exited
#                             process, is no gap.
#  85. Default changed      -> origin's HEAD naming another branch before the
#                             removal keeps a merged worktree and branch.
#  84. Unreadable subdir    -> the nested-repository walk keeps the worktree.
#
# Run: bash skills/herdr-foreman/tests/test_prune_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() {
  # `ps -p` exits 1 for a pid that is gone and prints nothing on stderr.
  if [[ -n "${SLEEPER:-}" ]] && ps -p "$SLEEPER" >/dev/null; then kill "$SLEEPER" || echo "warn: could not stop sleeper $SLEEPER" >&2; fi
  [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2
  return 0
}
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Run a fixture command, stderr captured: on failure the harness stops with
# the command's own words and the command to rerun, never a silent exit.
quiet() { # <what> <command...>
  local what="$1" rc=0; shift
  "$@" 2>"$TMP/quiet.err" || rc=$?
  if (( rc != 0 )); then
    die "${what} (exit ${rc}): $(tr '\n' ' ' < "$TMP/quiet.err") — rerun \`$*\` by hand to see the whole failure"
  fi
}

mk_repo() { # <prefix> -> sets SHARED, SEED, BARE
  local prefix="$1"
  BARE="$TMP/${prefix}.git"
  SEED="$TMP/${prefix}-seed"
  git init -q --bare -b main "$BARE"            || die "git init --bare failed"
  quiet "git clone failed" git clone -q "$BARE" "$SEED"
  printf 'x\n' > "$SEED/f"                      || die "seed write failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t add f  || die "git add failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t commit -q -m c1 || die "git commit failed"
  git -C "$SEED" push -q origin main            || die "git push failed"
  SHARED="$TMP/${prefix}-shared"
  quiet "git clone (shared) failed" git clone -q "$BARE" "$SHARED"
  quiet "git remote set-head failed" git -C "$SHARED" remote set-head origin --auto >/dev/null
  # Every case gets its own worktree root: no case sees another's worktrees.
  ROOT="$TMP/root-${prefix}"
  mkdir -p "$ROOT" || die "cannot create the worktree root $ROOT"
}

add_wt() { # <shared> <branch> <path>  (cut at main)
  quiet "worktree add $2 failed" git -C "$1" worktree add -q -b "$2" "$3" origin/main
}

commit_in() { # <worktree> <file>
  printf 'y\n' > "$1/$2" || die "write $2 failed"
  git -C "$1" -c user.name=t -c user.email=t@t add "$2" || die "git add in $1 failed"
  git -C "$1" -c user.name=t -c user.email=t@t commit -q -m "c-$2" || die "git commit in $1 failed"
}

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
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



# Start a background process whose cwd is <dir>, and return only once it is
# there: the child writes its physical cwd into a FIFO after the cd, then
# execs sleep; the harness's read of that FIFO returns exactly then, with no
# polling and no deadline. The process is registered with the fake lsof.
start_sleeper() { # <dir>
  local want ready="$TMP/sleeper.ready" got
  want="$(cd "$1" && pwd -P && printf x)" || die "cannot resolve the sleeper's directory $1"
  want="${want%x}"; want="${want%$'\n'}"
  rm -f "$ready" || die "cannot clear the sleeper's FIFO $ready"
  mkfifo "$ready" || die "mkfifo $ready failed"
  (
    if cd "$1"; then
      { pwd -P; printf x; } > "$ready"
      exec sleep 3600
    fi
    printf 'cd-failed' > "$ready"
  ) &
  SLEEPER=$!
  got="$(cat "$ready")" || die "cannot read the sleeper's FIFO $ready"
  [[ "$got" == *x ]] || die "the sleeper could not enter $1: $got"
  got="${got%x}"; got="${got%$'\n'}"
  [[ "$got" == "$want" ]] || die "the sleeper reported cwd '$got', not '$want'"
  # Registered with the fake probe; a path it cannot hold on one line is one
  # the script refuses to match before probing at all.
  if [[ "$want" != *$'\n'* ]]; then
    printf '%s %s\n' "$SLEEPER" "$want" >> "$FAKE_LSOF_CWDS" || die "cannot register the sleeper with the fake lsof"
  fi
}

kept_field() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r.get(sys.argv[2], "") for r in d["worktrees_kept"] if r["path"]==sys.argv[1]), ""))' "$1" "$2" <<<"$OUT"; }
removed_head() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["head"] for r in d["worktrees_removed"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
lock_reason_of() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r.get("lock_reason") or "" for r in d["worktrees_kept"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
# An lsof stand-in: silent until its <n>th call, then reporting a process
# working inside <path>.
lsof_turns_busy() { # <dir> <n> <path>
  mkdir -p "$1" || die "mkdir $1 failed"
  # shellcheck disable=SC2016  # The stand-in's $(...) must run in the stand-in, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf x >> %q\nif (( $(wc -c < %q) >= %s )); then printf "p1\\0\\nfcwd\\0n%%s\\0\\n" %q; fi\n' \
    "$1/calls" "$1/calls" "$2" "$3" > "$1/lsof" || die "write lsof stand-in failed"
  chmod +x "$1/lsof" || die "chmod lsof stand-in failed"
}

# Stop the background sleeper: SIGTERM, then its exit status must be 143
# (128 + SIGTERM); anything else means the fixture did not behave as assumed.
stop_sleeper() {
  local st=0
  kill "$SLEEPER" || die "could not stop the sleeper $SLEEPER"
  wait "$SLEEPER" || st=$?
  case "$st" in
    143) SLEEPER=""; : > "$FAKE_LSOF_CWDS" || die "cannot clear the fake lsof's records" ;;
    *) die "the sleeper $SLEEPER ended with status $st, not 143 (SIGTERM)" ;;
  esac
}
# The process probe every run uses: a stand-in for lsof that prints one
# NUL-framed cwd record per "<pid> <cwd>" line of $FAKE_LSOF_CWDS and nothing
# else, so the host's own process table never reaches a test. A case that
# needs a process inside a worktree registers it there; a case exercising the
# probe's failure modes names its own stand-in through PRUNE_LSOF.
write_fake_lsof() { # <path>
  mkdir -p "$(dirname "$1")" || die "mkdir for the fake lsof failed"
  cat > "$1" <<'SH' || die "write the fake lsof failed"
#!/usr/bin/env bash
set -euo pipefail
list="${FAKE_LSOF_CWDS:-}"
if [[ -z "$list" || ! -s "$list" ]]; then exit 0; fi
while IFS=' ' read -r pid cwd; do
  printf 'p%s\0\nfcwd\0n%s\0\n' "$pid" "$cwd"
done < "$list"
SH
  chmod +x "$1" || die "chmod the fake lsof failed"
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
  write_fake_lsof "$TMP/fake-lsof/lsof"
  export PRUNE_LSOF="$TMP/fake-lsof/lsof" FAKE_LSOF_CWDS="$TMP/fake-lsof/cwds"
  ROOT="$TMP/worktrees"
  mkdir -p "$ROOT" || die "mkdir root failed"
  real_git="$(command -v git)" || die "git not found on PATH — install git to run these tests"

  # --- 1, 3, 4, 5, 6, 7, 9, 10 share one repo: one run decides them all.
  mk_repo one
  add_wt "$SHARED" review/merged "$ROOT/one-merged"
  add_wt "$SHARED" test/unmerged "$ROOT/one-unmerged"; commit_in "$ROOT/one-unmerged" u
  add_wt "$SHARED" test/untracked "$ROOT/one-untracked"; printf 'z\n' > "$ROOT/one-untracked/scratch" || die "write failed"
  git -C "$SHARED" config status.showUntrackedFiles no || die "config failed"
  add_wt "$SHARED" test/modified "$ROOT/one-modified"; printf 'changed\n' > "$ROOT/one-modified/f" || die "write failed"
  quiet "detached add failed" git -C "$SHARED" worktree add -q --detach "$ROOT/one-detached" origin/main
  commit_in "$ROOT/one-detached" d
  add_wt "$SHARED" test/locked "$ROOT/one-locked"; git -C "$SHARED" worktree lock "$ROOT/one-locked" || die "lock failed"
  git -C "$SHARED" branch --no-track merged-no-wt origin/main || die "branch failed"
  git -C "$SHARED" branch --no-track unmerged-no-wt origin/main || die "branch failed"
  quiet "scratch add failed" git -C "$SHARED" worktree add -q "$TMP/one-scratch" unmerged-no-wt
  commit_in "$TMP/one-scratch" w
  git -C "$SHARED" worktree remove "$TMP/one-scratch" || die "scratch remove failed"

  run "$SHARED"
  echo "1. merged + clean worktree is removed and its branch deleted"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/one-merged"* ]] && [[ ! -e "$ROOT/one-merged" ]] && ! has_branch "$SHARED" review/merged; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "3. an idle worktree with unpushed commits is kept with reason unpushed"
  if [[ "$(kept_reason "$ROOT/one-unmerged")" == unpushed ]] && [[ -d "$ROOT/one-unmerged" ]] && has_branch "$SHARED" test/unmerged; then pass; else fail "out=$OUT"; fi
  echo "4. untracked file keeps the worktree as dirty, even with status.showUntrackedFiles=no"
  if [[ "$(kept_reason "$ROOT/one-untracked")" == dirty ]] && [[ -f "$ROOT/one-untracked/scratch" ]]; then pass; else fail "out=$OUT"; fi
  echo "5. modified file keeps the worktree as dirty"
  if [[ "$(kept_reason "$ROOT/one-modified")" == dirty ]] && [[ -d "$ROOT/one-modified" ]]; then pass; else fail "out=$OUT"; fi
  echo "6. a detached worktree holding an unpushed commit is kept"
  if [[ "$(kept_reason "$ROOT/one-detached")" == unpushed ]] && [[ -d "$ROOT/one-detached" ]]; then pass; else fail "out=$OUT"; fi
  echo "7. locked worktree is kept"
  if [[ "$(kept_reason "$ROOT/one-locked")" == locked ]] && [[ -d "$ROOT/one-locked" ]]; then pass; else fail "out=$OUT"; fi
  echo "9. the shared checkout is never listed and the default branch survives"
  if ! mentions_path "$SHARED" && has_branch "$SHARED" main && [[ "$(field default_branch)" == main ]]; then pass; else fail "out=$OUT"; fi
  echo "10. merged branch without a worktree is deleted; unmerged one is kept"
  if [[ "$(branches_deleted)" == *merged-no-wt* ]] && ! has_branch "$SHARED" merged-no-wt && [[ "$(branch_kept_reason unmerged-no-wt)" == unpushed ]] && has_branch "$SHARED" unmerged-no-wt; then pass; else fail "out=$OUT"; fi

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
  quiet "outside add failed" git -C "$SHARED" worktree add -q -b review/outside "$TMP/eight-outside" origin/main
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
  run_with_shim() { RUN_SEQ=$((RUN_SEQ+1)); OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"; }
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
  quiet "shadow branch failed" git -C "$SHARED" branch --no-track origin/main origin/main
  run "$SHARED"
  echo "19. ancestry is judged on fully qualified refs: a same-name tag or an origin/main local branch cannot shadow"
  if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/nineteen-shadow")" == unpushed ]] && [[ -d "$ROOT/nineteen-shadow" ]] && has_branch "$SHARED" review/shadow; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim21:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim22:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "22. the JSON reports the removal that happened even when the branch deletion fails"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$ROOT/twentytwo-halfway"* ]] && [[ ! -e "$ROOT/twentytwo-halfway" ]] && [[ "$OUT" == *'"failed": [{'*"deleting"* ]] && [[ "$OUT" == *"fixture refuses the deletion"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 23. a prunable branch is deferred when the metadata removal is skipped.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twentythree
    add_wt "$SHARED" review/vanished "$ROOT/twentythree-vanished"
    rm -rf "$ROOT/twentythree-vanished" || die "rm failed"
    add_wt "$SHARED" review/sealed23 "$ROOT/twentythree-sealed"
    chmod 000 "$ROOT/twentythree-sealed" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/twentythree-sealed" || die "chmod restore failed"
    echo "23. a prunable branch is not deleted while its metadata survives a skipped metadata removal"
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
  quiet "fixture could not create a tracking worktree" \
    git -C "$SHARED" worktree add -q --track -b review/tracked "$ROOT/twentyfive-tracked" origin/main
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
  echo "26. a locked entry whose metadata stays does not release its branch"
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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim27:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim30:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim31:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
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
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim33:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "33. a failed post-deletion occupancy read is reported, not read as unoccupied"
  if (( RC == 2 )) && [[ "$OUT" == *"could not be read"* ]] && [[ "$OUT" == *"fixture inventory failure"* ]] && [[ "$(branches_deleted)" != *review/unreadable* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 32. a parent whose own name ends in a newline still confirms absence.
  mk_repo thirtytwo
  nl_parent="$ROOT/thirtytwo-p"$'\n'
  mkdir -p "$nl_parent" || die "mkdir newline parent failed"
  quiet "fixture could not create a worktree under a newline-bearing parent" \
    git -C "$SHARED" worktree add -q -b review/nlparent "$nl_parent/wt" origin/main
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
      if ! "$(command -v git)" -C "$SHARED" worktree add -q --track -b review/recreated "$ROOT/thirtyfour-live" origin/main >/dev/null 2>"$TMP/shim34/add.err"; then
        echo "shim34: fixture could not recreate review/recreated: \$(cat "$TMP/shim34/add.err")" >&2
      fi
    fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim34/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim34:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
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
  quiet "detached worktree add failed" git -C "$SHARED" worktree add -q --detach "$det" origin/main
  add_wt "$SHARED" review/pushed "$pushed"; commit_in "$pushed" p.txt
  quiet "push review/pushed failed" git -C "$pushed" push -q origin review/pushed
  git -C "$SHARED" fetch -q origin || die "fetch after push failed"
  quiet "busy worktree add failed" git -C "$SHARED" worktree add -q --detach "$busy" origin/main
  add_wt "$SHARED" feat/stale "$stale"; commit_in "$stale" s.txt
  printf 'untracked work\n' > "$stale/notes.txt" || die "untracked write failed"
  quiet "held worktree add failed" git -C "$SHARED" worktree add -q --detach "$held" origin/main
  git -C "$SHARED" worktree lock --reason "Active Herdr reviewer" "$held" || die "worktree lock failed"
  local stale_tip
  stale_tip="$(git -C "$stale" rev-parse HEAD)" || die "rev-parse stale HEAD failed"
  start_sleeper "$busy"
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
  echo "37. an idle worktree with unpushed commits and untracked work is kept and reported, never touched"
  if [[ "$(kept_reason "$stale")" == dirty ]] && listed "$SHARED" "$stale" && [[ -e "$stale/notes.txt" ]] \
    && [[ "$(kept_field "$stale" dirty_files)" == 1 ]] && (( $(kept_field "$stale" age_hours) >= 24 )) && [[ "$(kept_field "$stale" command)" == "git -C $stale status" ]] \
    && has_branch "$SHARED" feat/stale && [[ "$(git -C "$SHARED" rev-parse feat/stale)" == "$stale_tip" ]]; then
    pass; else fail "dirty kept: out=$OUT err=$ERRTEXT"; fi
  echo "38. a locked idle worktree is kept with its lock reason"
  if [[ "$(kept_reason "$held")" == locked && "$(lock_reason_of "$held")" == "Active Herdr reviewer" ]] && listed "$SHARED" "$held"; then
    pass; else fail "locked: out=$OUT"; fi
  git -C "$SHARED" worktree unlock "$held" || die "worktree unlock failed"

  # --- 39-40: fresh activity keeps a worktree; an idle clean unpushed one is reported.
  mk_repo fresh
  local fresh_det="$ROOT/fresh-detached" mid="$ROOT/fresh-mid"
  quiet "fresh worktree add failed" git -C "$SHARED" worktree add -q --detach "$fresh_det" origin/main
  add_wt "$SHARED" feat/mid "$mid"; commit_in "$mid" m.txt
  age_wt "$mid"
  idle_run
  echo "39. a reachable worktree with fresh activity is kept"
  if [[ "$(kept_reason "$fresh_det")" == not-idle ]] && listed "$SHARED" "$fresh_det"; then
    pass; else fail "not idle: out=$OUT"; fi
  echo "40. an idle clean worktree with unpushed commits is kept with its count and push command"
  if [[ "$(kept_reason "$mid")" == unpushed ]] && listed "$SHARED" "$mid" \
    && [[ "$(kept_field "$mid" unpushed_commits)" == 1 ]] && (( $(kept_field "$mid" age_hours) >= 24 )) && [[ "$(kept_field "$mid" command)" == "git -C $mid push -u origin HEAD" ]]; then
    pass; else fail "unpushed kept: out=$OUT"; fi

  # --- 41. no process probe: nothing is judged idle.
  mk_repo noprobe
  local np="$ROOT/noprobe-detached"
  quiet "noprobe worktree add failed" git -C "$SHARED" worktree add -q --detach "$np" origin/main
  age_wt "$np"
  idle_run PRUNE_LSOF="$TMP/no-such-lsof"
  echo "41. a missing process probe keeps an idle worktree as idle-unknown"
  if (( RC == 0 )) && [[ "$(kept_reason "$np")" == idle-unknown ]] && listed "$SHARED" "$np" && [[ "$ERRTEXT" == *"install lsof"* ]]; then
    pass; else fail "no probe: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 43. a dry run previews the removal and changes nothing.
  mk_repo dryidle
  local dd="$ROOT/dryidle-detached" ds="$ROOT/dryidle-stale"
  quiet "dryidle worktree add failed" git -C "$SHARED" worktree add -q --detach "$dd" origin/main
  add_wt "$SHARED" feat/drystale "$ds"; commit_in "$ds" d.txt
  age_wt "$dd"; age_wt "$ds"
  IDLE_ARGS=(--dry-run)
  idle_run
  IDLE_ARGS=()
  echo "43. a dry run previews the removal and changes nothing"
  if (( RC == 0 )) && [[ -n "$(removed_head "$dd")" && "$(kept_reason "$ds")" == unpushed ]] \
    && listed "$SHARED" "$dd" && listed "$SHARED" "$ds" && has_branch "$SHARED" feat/drystale; then
    pass; else fail "dry run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 44-45: the merged path waits for idleness and an empty worktree too.
  mk_repo mergedidle
  local mfresh="$ROOT/mergedidle-fresh" mbusy="$ROOT/mergedidle-busy"
  add_wt "$SHARED" review/mfresh "$mfresh"
  add_wt "$SHARED" review/mbusy "$mbusy"
  age_wt "$mbusy"
  start_sleeper "$mbusy"
  idle_run
  stop_sleeper
  echo "44. a clean merged worktree with fresh activity is kept"
  if [[ "$(kept_reason "$mfresh")" == not-idle ]] && listed "$SHARED" "$mfresh" && has_branch "$SHARED" review/mfresh; then
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
  echo "46. a fresh edit to a tracked file keeps an otherwise old worktree as not yet idle"
  if [[ "$(kept_reason "$fe")" == not-idle ]] && listed "$SHARED" "$fe"; then
    pass; else fail "fresh edit: out=$OUT err=$ERRTEXT"; fi

  # --- 47. a process arriving between the judgment and the removal keeps it.
  mk_repo race
  local rw="$ROOT/race-detached"
  quiet "race worktree add failed" git -C "$SHARED" worktree add -q --detach "$rw" origin/main
  age_wt "$rw"
  lsof_turns_busy "$TMP/lsof47" 2 "$rw"
  idle_run PRUNE_LSOF="$TMP/lsof47/lsof"
  echo "47. a worktree that turns busy before its removal is kept"
  if (( RC == 0 )) && [[ "$(kept_reason "$rw")" == changed ]] && listed "$SHARED" "$rw" && [[ "$ERRTEXT" == *"process is now working inside"* ]]; then
    pass; else fail "changed at removal: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 47b. the dry run takes the same recheck before previewing a removal.
  mk_repo racedry
  local rdw="$ROOT/racedry-detached"
  quiet "racedry worktree add failed" git -C "$SHARED" worktree add -q --detach "$rdw" origin/main
  age_wt "$rdw"
  lsof_turns_busy "$TMP/lsof47b" 2 "$rdw"
  IDLE_ARGS=(--dry-run)
  idle_run PRUNE_LSOF="$TMP/lsof47b/lsof"
  IDLE_ARGS=()
  echo "47b. a dry run keeps a worktree that turns busy before the preview, as the live run would"
  if (( RC == 0 )) && [[ "$(kept_reason "$rdw")" == changed ]] && [[ -z "$(removed_head "$rdw")" ]] && listed "$SHARED" "$rdw"; then
    pass; else fail "dry-run recheck: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 59. a path lsof cannot report faithfully is never judged idle.
  mk_repo newline
  local nlwt="$ROOT/nl"$'\n'"wt" nlbusy="$ROOT/nlb"$'\n'"busy"
  quiet "newline worktree add failed" git -C "$SHARED" worktree add -q --detach "$nlwt" origin/main
  quiet "newline busy worktree add failed" git -C "$SHARED" worktree add -q --detach "$nlbusy" origin/main
  age_wt "$nlwt"; age_wt "$nlbusy"
  start_sleeper "$nlbusy"
  idle_run
  stop_sleeper
  echo "59. worktrees whose path holds a newline are kept as idle-unknown, a process inside or not"
  if (( RC == 0 )) && [[ "$(kept_reason "$nlwt")" == idle-unknown && "$(kept_reason "$nlbusy")" == idle-unknown ]] \
    && listed "$SHARED" "$nlwt" && listed "$SHARED" "$nlbusy"; then
    pass; else fail "newline: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 51. a dirty submodule keeps an idle worktree.
  mk_repo submod
  local sub_origin="$TMP/sublib.git" sw="$ROOT/submod-wt"
  git init -q --bare -b main "$sub_origin" || die "sub origin init failed"
  quiet "sub seed clone failed" git clone -q "$sub_origin" "$TMP/sublib-seed"
  printf 's\n' > "$TMP/sublib-seed/s" || die "sub seed write failed"
  git -C "$TMP/sublib-seed" -c user.name=t -c user.email=t@t add s || die "sub add failed"
  git -C "$TMP/sublib-seed" -c user.name=t -c user.email=t@t commit -q -m s1 || die "sub commit failed"
  quiet "sub push failed" git -C "$TMP/sublib-seed" push -q origin main
  add_wt "$SHARED" feat/submod "$sw"
  quiet "submodule add failed" git -C "$sw" -c protocol.file.allow=always submodule --quiet add "$sub_origin" lib
  git -C "$sw" -c user.name=t -c user.email=t@t commit -q -m "add lib" || die "submodule commit failed"
  printf 'nested edit\n' >> "$sw/lib/s" || die "nested edit failed"
  age_wt "$sw"
  idle_run
  echo "51. a worktree whose submodule holds changes is kept"
  if [[ "$(kept_reason "$sw")" == submodule-dirty ]] && listed "$SHARED" "$sw"; then
    pass; else fail "submodule: out=$OUT err=$ERRTEXT"; fi

  # --- 60. only origin's refs prove reachability.
  mk_repo otherremote
  local orw="$ROOT/otherremote-wt"
  git init -q --bare -b main "$TMP/upstream.git" || die "upstream init failed"
  git -C "$SHARED" remote add upstream "$TMP/upstream.git" || die "remote add failed"
  quiet "otherremote worktree add failed" git -C "$SHARED" worktree add -q --detach "$orw" origin/main
  commit_in "$orw" u.txt
  quiet "push upstream failed" git -C "$orw" push -q upstream HEAD:refs/heads/side
  git -C "$SHARED" fetch -q upstream || die "fetch upstream failed"
  age_wt "$orw"
  idle_run
  echo "60. a HEAD held only by another remote's ref is not reachable"
  if [[ "$(kept_reason "$orw")" == unpushed ]] && listed "$SHARED" "$orw"; then
    pass; else fail "other remote: out=$OUT err=$ERRTEXT"; fi

  # --- 61. the reachability proof is re-derived just before the removal.
  mk_repo proofgone
  local pg="$ROOT/proofgone-wt"
  quiet "proofgone worktree add failed" git -C "$SHARED" worktree add -q --detach "$pg" origin/main
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
  local gl="$ROOT/gitlink-wt" nr="$ROOT/gitlink-nested" sub62="$TMP/sublib62.git"
  git init -q --bare -b main "$sub62" || die "sub62 origin init failed"
  quiet "sub62 seed clone failed" git clone -q "$sub62" "$TMP/sublib62-seed"
  printf 's\n' > "$TMP/sublib62-seed/s" || die "sub62 seed write failed"
  git -C "$TMP/sublib62-seed" -c user.name=t -c user.email=t@t add s || die "sub62 add failed"
  git -C "$TMP/sublib62-seed" -c user.name=t -c user.email=t@t commit -q -m s1 || die "sub62 commit failed"
  quiet "sub62 push failed" git -C "$TMP/sublib62-seed" push -q origin main
  add_wt "$SHARED" feat/gitlink "$gl"
  quiet "submodule add failed" git -C "$gl" -c protocol.file.allow=always submodule --quiet add "$sub62" lib
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
  if [[ "$(kept_reason "$ig")" == nested-repo && "$(kept_reason "$deep")" == nested-repo ]] && listed "$SHARED" "$ig" && listed "$SHARED" "$deep"; then
    pass; else fail "hidden nested repos: out=$OUT err=$ERRTEXT"; fi

  # --- 69. dry run and live run agree after origin deletes a branch.
  mk_repo agree
  local ag="$ROOT/agree-wt"
  quiet "agree worktree add failed" git -C "$SHARED" worktree add -q --detach "$ag" origin/main
  commit_in "$ag" gone.txt
  quiet "push side failed" git -C "$ag" push -q origin HEAD:refs/heads/side
  git -C "$SHARED" fetch -q origin || die "fetch side failed"
  quiet "delete side on origin failed" git -C "$SEED" push -q origin --delete side
  age_wt "$ag"
  IDLE_ARGS=(--dry-run)
  idle_run
  IDLE_ARGS=()
  local dry_reason; dry_reason="$(kept_reason "$ag")"
  idle_run
  echo "69. a HEAD held only by an origin branch deleted since the last fetch is kept by both dry and live runs"
  if [[ "$dry_reason" == unpushed && "$(kept_reason "$ag")" == unpushed ]] && listed "$SHARED" "$ag"; then
    pass; else fail "dry/live agreement: dry=$dry_reason out=$OUT err=$ERRTEXT"; fi

  # --- 71. a force-push that drops the merge between judgment and removal keeps it.
  mk_repo forcepush
  local fp="$ROOT/forcepush-wt" fp_base
  fp_base="$(git -C "$SHARED" rev-parse origin/main)" || die "rev-parse base failed"
  add_wt "$SHARED" review/fp "$fp"; commit_in "$fp" fp.txt
  quiet "push to main failed" git -C "$fp" push -q origin HEAD:main
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

  # --- 80. a branch with no worktree whose tip an origin branch holds is deleted.
  mk_repo pushedbranch
  local pb="$ROOT/pushedbranch-wt"
  add_wt "$SHARED" feat/pushed "$pb"; commit_in "$pb" p.txt
  quiet "push feat/pushed failed" git -C "$pb" push -q origin feat/pushed
  quiet "remove the worktree by hand failed" git -C "$SHARED" worktree remove "$pb"
  run "$SHARED"
  echo "80. a branch with no worktree whose tip origin holds is deleted"
  if (( RC == 0 )) && [[ "$(branches_deleted)" == *feat/pushed* ]] && ! has_branch "$SHARED" feat/pushed; then
    pass; else fail "origin-held branch: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 81. an idle unpushed branch is reported; a fresh one is not-idle.
  #         Every commit and reflog entry carries a fixed date (git stamps
  #         reflog entries with GIT_COMMITTER_DATE), judged at IDLE_NOW.
  mk_repo localonly
  local old_at="2020-01-01T00:00:00Z" new_at="2020-01-09T20:00:00Z"
  GIT_COMMITTER_DATE="$old_at" quiet "old branch failed" git -C "$SHARED" branch --no-track feat/old origin/main
  GIT_COMMITTER_DATE="$new_at" quiet "new branch failed" git -C "$SHARED" branch --no-track feat/new origin/main
  local c_old c_new
  c_old="$(GIT_AUTHOR_DATE="$old_at" GIT_COMMITTER_DATE="$old_at" git -C "$SHARED" -c user.name=t -c user.email=t@t commit-tree "origin/main^{tree}" -p origin/main -m old)" || die "old commit failed"
  c_new="$(GIT_AUTHOR_DATE="$new_at" GIT_COMMITTER_DATE="$new_at" git -C "$SHARED" -c user.name=t -c user.email=t@t commit-tree "origin/main^{tree}" -p origin/main -m new)" || die "new commit failed"
  GIT_COMMITTER_DATE="$old_at" quiet "move feat/old failed" git -C "$SHARED" update-ref -m old refs/heads/feat/old "$c_old"
  GIT_COMMITTER_DATE="$new_at" quiet "move feat/new failed" git -C "$SHARED" update-ref -m new refs/heads/feat/new "$c_new"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$IDLE_NOW" PRUNE_IDLE_HOURS=24 bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  local kept_old; kept_old="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((json.dumps(b, sort_keys=True) for b in d["branches_kept"] if b["branch"]=="feat/old"), ""))' <<<"$OUT")"
  echo "81. an idle unpushed branch is reported with its count, age and push command, and left in place"
  if (( RC == 0 )) && [[ "$kept_old" == *'"reason": "unpushed"'* && "$kept_old" == *'"unpushed_commits": 1'* && "$kept_old" == *'"age_hours": 216'* && "$kept_old" == *'push -u origin feat/old'* ]] \
    && has_branch "$SHARED" feat/old; then
    pass; else fail "unpushed branch: rc=$RC kept=$kept_old out=$OUT err=$ERRTEXT"; fi
  echo "81b. a branch with activity inside the window is not-idle, and left in place"
  if [[ "$(branch_kept_reason feat/new)" == not-idle ]] && has_branch "$SHARED" feat/new; then
    pass; else fail "fresh branch: out=$OUT"; fi

  # --- 82. a merge dropped from origin before a branch-pass deletion keeps the branch.
  mk_repo branchproof
  local bp="$ROOT/branchproof-wt" bp_base
  bp_base="$(git -C "$SHARED" rev-parse origin/main)" || die "rev-parse base failed"
  add_wt "$SHARED" review/bp "$bp"; commit_in "$bp" bp.txt
  quiet "push to main failed" git -C "$bp" push -q origin HEAD:main
  quiet "fetch after merge failed" git -C "$SHARED" fetch -q origin
  quiet "remove bp worktree failed" git -C "$SHARED" worktree remove "$bp"
  mkdir -p "$TMP/shim82" || die "mkdir shim82 failed"
  # shellcheck disable=SC2016  # The shim's "$@" and $(...) must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *ls-remote*refs/heads/main* ]]; then printf x >> %q; if [[ "$(cat %q)" == xx ]]; then printf "%%s\\trefs/heads/main\\n" %q; exit 0; fi; fi\nexec %q "$@"\n' \
    "$TMP/shim82/n" "$TMP/shim82/n" "$bp_base" "$real_git" > "$TMP/shim82/git" || die "shim82 write failed"
  chmod +x "$TMP/shim82/git" || die "chmod shim82 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim82:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "82. a merge dropped from origin just before the branch deletion keeps the branch"
  if (( RC == 0 )) && [[ "$(branch_kept_reason review/bp)" == changed ]] && has_branch "$SHARED" review/bp; then
    pass; else fail "branch proof: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 86. origin dropping the commit while the worktree is removed keeps the branch.
  mk_repo wtproof
  local wp="$ROOT/wtproof-wt"
  add_wt "$SHARED" review/wp "$wp"; commit_in "$wp" wp.txt
  quiet "push review/wp failed" git -C "$wp" push -q origin HEAD:review/wp
  quiet "fetch after push failed" git -C "$SHARED" fetch -q origin
  mkdir -p "$TMP/shim86" || die "mkdir shim86 failed"
  # The real removal runs, then origin loses the branch: the window between
  # the first proof and the branch deletion. A failed drop breaks the premise.
  # shellcheck disable=SC2016  # The shim's "$@" and $? must expand in the shim, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *"worktree remove"* ]]; then rc=0; %q "$@" || rc=$?; %q -C %q update-ref -d refs/heads/review/wp || { echo "shim86: could not drop review/wp" >&2; exit 1; }; exit "$rc"; fi\nexec %q "$@"\n' \
    "$real_git" "$real_git" "$BARE" "$real_git" > "$TMP/shim86/git" || die "shim86 write failed"
  chmod +x "$TMP/shim86/git" || die "chmod shim86 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$TMP/shim86:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "86. origin dropping the commit while its worktree is removed keeps the branch, exit 2"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$wp"* ]] && [[ "$OUT" == *"origin stopped holding"* ]] && has_branch "$SHARED" review/wp; then
    pass; else fail "worktree proof: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 83. a partial lsof listing (exit 0, an unreadable cwd) is not an answer.
  mk_repo lsofpartial
  local lp="$ROOT/lsofpartial-detached"
  quiet "lsofpartial worktree add failed" git -C "$SHARED" worktree add -q --detach "$lp" origin/main
  age_wt "$lp"
  mkdir -p "$TMP/lsof83" || die "mkdir lsof83 failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "p1\\0\\nfcwd\\0n/ (readlink: Permission denied)\\0\\n"\n' > "$TMP/lsof83/lsof" || die "write lsof83 failed"
  chmod +x "$TMP/lsof83/lsof" || die "chmod lsof83 failed"
  idle_run PRUNE_LSOF="$TMP/lsof83/lsof"
  echo "83. an lsof listing with an unreadable cwd keeps the worktree as idle-unknown"
  if (( RC == 0 )) && [[ "$(kept_reason "$lp")" == idle-unknown ]] && listed "$SHARED" "$lp" && [[ "$ERRTEXT" == *"incomplete listing"* ]]; then
    pass; else fail "partial lsof: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 83b. a mount lsof could not stat is no gap in the cwd records.
  mk_repo lsofmount
  local lm="$ROOT/lsofmount-detached"
  quiet "lsofmount worktree add failed" git -C "$SHARED" worktree add -q --detach "$lm" origin/main
  age_wt "$lm"
  mkdir -p "$TMP/lsof83b" || die "mkdir lsof83b failed"
  cat > "$TMP/lsof83b/lsof" <<'SH' || die "write lsof83b failed"
#!/usr/bin/env bash
set -euo pipefail
printf 'p1\0\nfcwd\0n/\0\n'
printf '%s\n' "lsof: WARNING: can't stat() smbfs file system /Volumes/backup" \
  "      Output information may be incomplete." \
  '      assuming "dev=36000040" from mount table' >&2
SH
  chmod +x "$TMP/lsof83b/lsof" || die "chmod lsof83b failed"
  idle_run PRUNE_LSOF="$TMP/lsof83b/lsof"
  echo "83b. an lsof warning about an unstattable mount still judges the worktree idle"
  if (( RC == 0 )) && ! listed "$SHARED" "$lm" && [[ -n "$(removed_head "$lm")" ]] && [[ "$ERRTEXT" == *"could not stat"* ]]; then
    pass; else fail "mount warning: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 83c. an unreadable cwd of a process that has since exited is no gap.
  mk_repo lsofgone
  local lg="$ROOT/lsofgone-detached" gone
  quiet "lsofgone worktree add failed" git -C "$SHARED" worktree add -q --detach "$lg" origin/main
  age_wt "$lg"
  bash -c 'exit 0' &
  gone=$!
  wait "$gone" || die "the short-lived process failed"
  mkdir -p "$TMP/lsof83c" || die "mkdir lsof83c failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "p%s\\0\\nfcwd\\0n/ (readlink: No such process)\\0\\n"\n' "$gone" > "$TMP/lsof83c/lsof" || die "write lsof83c failed"
  chmod +x "$TMP/lsof83c/lsof" || die "chmod lsof83c failed"
  idle_run PRUNE_LSOF="$TMP/lsof83c/lsof"
  echo "83c. an unreadable cwd of an exited process still judges the worktree idle"
  if (( RC == 0 )) && ! listed "$SHARED" "$lg" && [[ -n "$(removed_head "$lg")" ]]; then
    pass; else fail "exited process: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 85. origin's default branch changing mid-run voids a merged proof.
  mk_repo newdefault
  local nd="$ROOT/newdefault-wt"
  add_wt "$SHARED" review/nd "$nd"
  age_wt "$nd"
  quiet "branch without a worktree failed" git -C "$SHARED" branch --no-track review/nd-nowt origin/main
  quiet "push other failed" git -C "$SHARED" push -q origin origin/main:refs/heads/other
  mkdir -p "$TMP/lsof85" || die "mkdir lsof85 failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\ngit -C %q symbolic-ref HEAD refs/heads/other\n' "$BARE" > "$TMP/lsof85/lsof" || die "write lsof85 failed"
  chmod +x "$TMP/lsof85/lsof" || die "chmod lsof85 failed"
  idle_run PRUNE_LSOF="$TMP/lsof85/lsof"
  echo "85. a default branch that changes before the removal keeps the worktree and the branch"
  if (( RC == 0 )) && [[ "$(kept_reason "$nd")" == changed ]] && listed "$SHARED" "$nd" \
    && [[ "$(branch_kept_reason review/nd-nowt)" == changed ]] && has_branch "$SHARED" review/nd-nowt; then
    pass; else fail "default changed: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 84. a directory the nested-repository walk cannot read keeps the worktree.
  if [[ "$(id -u)" == 0 ]]; then
    echo "84. skipped: root reads every directory"
  else
    mk_repo walkerr
    local we="$ROOT/walkerr-wt"
    quiet "walkerr worktree add failed" git -C "$SHARED" worktree add -q --detach "$we" origin/main
    printf 'build/\n' > "$we/.git-info-exclude" || die "write exclude failed"
    mkdir -p "$(git -C "$we" rev-parse --git-common-dir)/info" || die "mkdir info failed"
    printf 'sealed/\n' >> "$(git -C "$we" rev-parse --git-common-dir)/info/exclude" || die "write info/exclude failed"
    rm "$we/.git-info-exclude" || die "rm scratch failed"
    mkdir -p "$we/sealed/inner" || die "mkdir sealed failed"
    age_wt "$we"
    chmod 000 "$we/sealed" || die "chmod sealed failed"
    idle_run
    chmod 755 "$we/sealed" || die "restore sealed failed"
    echo "84. an unreadable directory under the worktree keeps it, reported as a failure"
    if (( RC == 2 )) && listed "$SHARED" "$we" && [[ "$OUT" == *"cannot read its submodules or nested repositories"* ]]; then
      pass; else fail "walk error: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 87. a root replaced during the run stops every later destructive step.
  mk_repo eightyfive
  add_wt "$SHARED" review/a85 "$ROOT/a85"
  add_wt "$SHARED" review/b85 "$ROOT/b85"
  git -C "$SHARED" branch --no-track review/c85 origin/main || die "branch c85 failed"
  local shim85="$TMP/shim85" root85="$ROOT"
  mkdir -p "$shim85" || die "mkdir shim85 failed"
  # The first `worktree remove` runs for real, then swaps the root for an
  # empty directory of the same name: the later worktree, the removed one's
  # branch, the metadata removal and the branch pass all come after the swap.
  cat > "$shim85/git" <<SHIM || die "shim85 write failed"
#!/usr/bin/env bash
set -euo pipefail
if [[ "\$*" == *"worktree remove"* && ! -e "$shim85/done" ]]; then
  "$real_git" "\$@"
  : > "$shim85/done"
  mv "$root85" "$root85.moved"
  mkdir "$root85"
  exit 0
fi
exec "$real_git" "\$@"
SHIM
  chmod +x "$shim85/git" || die "chmod shim85 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$shim85:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "87. a root replaced during the run stops every later removal, deletion and metadata removal"
  if (( RC == 2 )) && [[ -e "$shim85/done" ]] && [[ "$OUT" == *"worktree root was replaced"* ]] \
    && listed "$SHARED" "$ROOT/b85" && [[ -d "$root85.moved/b85" ]] \
    && has_branch "$SHARED" review/a85 && has_branch "$SHARED" review/b85 && has_branch "$SHARED" review/c85; then
    pass; else fail "root replaced mid-run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 88. a root that is not the one the caller proved decides nothing.
  mk_repo eightysix
  add_wt "$SHARED" review/a86 "$ROOT/a86"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PRUNE_ROOT_ID="0:0" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "88. a root other than the one PRUNE_ROOT_ID names is exit 1, no JSON, nothing removed"
  if (( RC == 1 )) && [[ -z "$OUT" && -d "$ROOT/a86" && "$ERRTEXT" == *PRUNE_ROOT_ID* ]] && has_branch "$SHARED" review/a86; then
    pass; else fail "caller root id: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 89. a root that becomes unlistable during the run is a changed root.
  if [[ "$(id -u)" == 0 ]]; then
    echo "89. skipped: root lists every directory"
  else
    mk_repo eightynine
    add_wt "$SHARED" review/a89 "$ROOT/a89"
    add_wt "$SHARED" review/b89 "$ROOT/b89"
    local shim89="$TMP/shim89" root89="$ROOT"
    mkdir -p "$shim89" || die "mkdir shim89 failed"
    # The first `worktree remove` runs for real, then the root loses every
    # permission: same directory, same inode, but nothing below it is readable.
    cat > "$shim89/git" <<SHIM || die "shim89 write failed"
#!/usr/bin/env bash
set -euo pipefail
if [[ "\$*" == *"worktree remove"* && ! -e "$shim89/done" ]]; then
  "$real_git" "\$@"
  : > "$shim89/done"
  chmod 000 "$root89"
  exit 0
fi
exec "$real_git" "\$@"
SHIM
    chmod +x "$shim89/git" || die "chmod shim89 failed"
    RUN_SEQ=$((RUN_SEQ+1))
    OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$shim89:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
    ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
    chmod 755 "$root89" || die "restore root89 failed"
    echo "89. a root that becomes unlistable during the run stops every later destructive step"
    if (( RC == 2 )) && [[ -e "$shim89/done" ]] && [[ "$OUT" == *"worktree root was replaced or became unreadable"* ]] \
      && listed "$SHARED" "$ROOT/b89" && [[ -d "$ROOT/b89" ]] \
      && has_branch "$SHARED" review/a89 && has_branch "$SHARED" review/b89; then
      pass; else fail "root unlistable mid-run: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 90. a root renamed inside the metadata step keeps its live worktrees' entries.
  mk_repo ninety
  add_wt "$SHARED" review/gone90 "$ROOT/gone90"
  rm -rf "$ROOT/gone90" || die "rm gone90 failed"
  add_wt "$SHARED" review/live90 "$ROOT/live90"; commit_in "$ROOT/live90" live
  local shim90="$TMP/shim90" moved90="$ROOT.moved90"
  mkdir -p "$shim90" || die "mkdir shim90 failed"
  # The metadata step's first git call (a whole-repository `worktree prune`, a
  # per-entry `worktree remove`, or the admin-directory lookup) runs just after
  # the root's identity was re-proven; the shim renames the root first, inside
  # that window.
  cat > "$shim90/git" <<SHIM || die "shim90 write failed"
#!/usr/bin/env bash
set -euo pipefail
if [[ ( "\$*" == *"worktree prune"* || "\$*" == *"worktree remove"* || "\$*" == *"--git-common-dir"* ) && ! -e "$shim90/done" ]]; then
  : > "$shim90/done"
  mv "$ROOT" "$moved90"
fi
exec "$real_git" "\$@"
SHIM
  chmod +x "$shim90/git" || die "chmod shim90 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$shim90:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  if [[ -e "$moved90" ]]; then mv "$moved90" "$ROOT" || die "restore root after 90 failed"; fi
  echo "90. a root renamed during the metadata step keeps the registration of every worktree it holds"
  if [[ -e "$shim90/done" ]] && listed "$SHARED" "$ROOT/live90" && [[ -d "$ROOT/live90" ]] \
    && ! listed "$SHARED" "$ROOT/gone90" && has_branch "$SHARED" review/live90; then
    pass; else fail "root moved at the metadata step: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 91. a failed per-entry metadata removal keeps the entry and its branch.
  if [[ "$(id -u)" == 0 ]]; then
    echo "91. skipped: root writes into any directory"
  else
    mk_repo ninetyone
    add_wt "$SHARED" review/gone91 "$ROOT/gone91"
    rm -rf "$ROOT/gone91" || die "rm gone91 failed"
    # The admin directory refuses writes: git still reads it, the drop fails.
    chmod 555 "$SHARED/.git/worktrees" || die "chmod worktrees91 failed"
    run "$SHARED"
    chmod 755 "$SHARED/.git/worktrees" || die "restore worktrees91 failed"
    echo "91. a failed metadata removal leaves the entry registered and its branch undeleted"
    if (( RC == 2 )) && [[ "$OUT" == *"removing its stale registration failed"*"re-run the sweep"* ]] \
      && listed "$SHARED" "$ROOT/gone91" && has_branch "$SHARED" review/gone91 \
      && [[ "$(branches_deleted)" != *review/gone91* ]]; then
      pass; else fail "metadata removal fails: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 92. a worktree that reappears before the cleanup is never touched.
  mk_repo ninetytwo
  add_wt "$SHARED" review/back92 "$ROOT/back92"; commit_in "$ROOT/back92" kept92
  local tip92 aside92="$TMP/aside92" shim92="$TMP/shim92"
  tip92="$(git -C "$ROOT/back92" rev-parse HEAD)" || die "rev-parse back92 failed"
  mv "$ROOT/back92" "$aside92" || die "move back92 aside failed"
  mkdir -p "$shim92" || die "mkdir shim92 failed"
  # Gone at the inventory, back on disk when the cleanup's first git call runs.
  cat > "$shim92/git" <<SHIM || die "shim92 write failed"
#!/usr/bin/env bash
set -euo pipefail
if [[ ( "\$*" == *"worktree remove"* || "\$*" == *"--git-common-dir"* ) && ! -e "$shim92/done" ]]; then
  : > "$shim92/done"
  mv "$aside92" "$ROOT/back92"
fi
exec "$real_git" "\$@"
SHIM
  chmod +x "$shim92/git" || die "chmod shim92 failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PRUNE_IDLE_HOURS=0 PATH="$shim92:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "92. a worktree moved back before the cleanup keeps its files, commits and registration"
  if (( RC == 2 )) && [[ -e "$shim92/done" ]] && [[ -f "$ROOT/back92/kept92" ]] \
    && listed "$SHARED" "$ROOT/back92" && [[ "$OUT" == *"no longer a stale registration"* ]] \
    && [[ "$(git -C "$SHARED" rev-parse refs/heads/review/back92)" == "$tip92" ]] \
    && [[ "$(git -C "$ROOT/back92" rev-parse HEAD)" == "$tip92" ]]; then
    pass; else fail "path reappears: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 93. a vanished registration outside the root is dropped as well.
  mk_repo ninetythree
  add_wt "$SHARED" review/out93 "$TMP/outside93"
  rm -rf "$TMP/outside93" || die "rm outside93 failed"
  run "$SHARED"
  echo "93. a vanished registration outside the worktree root is dropped"
  if (( RC == 0 )) && [[ "$(kept_reason "$TMP/outside93")" == prunable ]] && ! listed "$SHARED" "$TMP/outside93"; then
    pass; else fail "stale outside root: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 94. a relative-path registration is found and dropped.
  mk_repo ninetyfour
  local rel94_rc=0
  git -C "$SHARED" worktree add -q --relative-paths -b review/rel94 "$ROOT/rel94" origin/main >"$TMP/rel94.out" 2>&1 || rel94_rc=$?
  if (( rel94_rc != 0 )) && grep -q -- '--relative-paths' "$TMP/rel94.out"; then
    echo "94. skipped: this git has no --relative-paths ($(tr '\n' ' ' < "$TMP/rel94.out"))"
  elif (( rel94_rc != 0 )); then
    die "worktree add --relative-paths failed: $(cat "$TMP/rel94.out")"
  else
    grep -q '^\.\.' "$SHARED/.git/worktrees/rel94/gitdir" || die "fixture gitdir is not relative: $(cat "$SHARED/.git/worktrees/rel94/gitdir")"
    rm -rf "$ROOT/rel94" || die "rm rel94 failed"
    run "$SHARED"
    echo "94. a vanished registration with a relative gitdir is dropped"
    if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/rel94")" == prunable ]] && ! listed "$SHARED" "$ROOT/rel94"; then
      pass; else fail "relative gitdir: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

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
