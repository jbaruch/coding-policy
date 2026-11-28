#!/usr/bin/env bash
# Outcome-based tests for skills/release/hooks/check-leftover-worktrees.sh.
#
# Each case builds its own bare "origin", a shared clone, its own worktree
# root, so no case sees another's state and they run in any order
# (rules/testing-standards.md Independence). Every commit and reflog entry is
# dated FIXTURE_DATE, every aged file is touched to a fixed mtime, and the
# owner script judges at PRUNE_NOW, never the wall clock (Determinism).
#
# The owner script's decisions have their own suite
# (tests/release-lib/tests/test_prune_worktrees.sh); these cases cover what the hook adds: it
# runs it live for this repository, from the shared checkout or a linked
# worktree, natively or under tessl, lists only what the operator must decide,
# and turns a failure into one "could not check" line.
#
# Covers:
#   1. Removable            -> an idle clean worktree origin holds, a merged
#                              local branch and an origin-held local branch
#                              are removed; origin is unchanged, nothing is printed.
#   2. Questionable         -> an idle dirty worktree, an idle unpushed
#                              worktree and an unpushed local branch are listed
#                              and left. A stale unmerged branch on origin and
#                              another repository's dirty worktree are not listed.
#   3. Not yet idle         -> a fresh dirty worktree is not listed.
#   4. In use               -> a process inside keeps a removable worktree,
#                              unlisted.
#   6. Linked worktree      -> a session in a linked worktree cleans the
#                              shared repository as the shared checkout does.
#   7. Portable mode        -> under tessl removable items go as natively, and
#                              the questionable list is printed.
#   8. No origin, no repo   -> silent.
#  12. Malformed result     -> an owner result missing documented fields is a
#                              could-not-check status.
#  11. Newline path         -> a shared checkout whose name ends in a newline
#                              is cleaned, never truncated.
#  10. Missing tool         -> a missing git or python3 still prints the
#                              could-not-check status, as fixed JSON.
#   9. Out of time          -> a could-not-check line naming the budget (a
#                              stand-in runner reports the timeout; no case
#                              waits on a clock, and every other run gets an
#                              hour).
#  14. Inventory failure    -> a git worktree list that fails after printing a
#                              valid prefix is a could-not-check status naming
#                              git's own exit, and does not run the owner script.
#  15. SSH BatchMode        -> appended to a user-set GIT_SSH_COMMAND, and the
#                              default when none is set.
#  16. Newline hooks dir    -> a hooks directory whose name ends in a newline
#                              still reaches its owner script.
#  17. Locked               -> a worktree locked with or without a reason stays,
#                              with its lock and branch, and is not listed.
#  18. Repository hooks     -> local cleanup runs without executing configured
#                              Git hooks or changing their configuration.
#  19. Stop fetch           -> dry-run cleanup fetches new refs without executing
#                              configured Git hooks or deleting local branches.
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash hooks/tests/test_check_leftover_worktrees.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${HERE}/../../skills/release/hooks/check-leftover-worktrees.sh"
PASS=0
FAIL=0
TMP=""
SLEEPER=""

#: Every fixture commit and reflog entry carries this date.
FIXTURE_DATE="2020-01-01T00:00:00Z"
AGED_MTIME=202001010000
#: A mtime inside the idle window of PRUNE_NOW in any time zone.
FRESH_MTIME=202001092000
#: 2020-01-10T00:00:00Z: nine days after FIXTURE_DATE.
PRUNE_NOW=1578614400

die() { echo "fatal: $*" >&2; exit 2; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() {
  stop_sleeper
  if [[ -n "$TMP" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi
  return 0
}

# Run a fixture command with stderr captured; a failure stops the harness
# with the command's own words.
quiet() { # <what> <command...>
  local what="$1" rc=0; shift
  "$@" 2>"$TMP/quiet.err" || rc=$?
  if (( rc != 0 )); then
    die "${what} (exit ${rc}): $(tr '\n' ' ' < "$TMP/quiet.err") — rerun \`$*\` by hand"
  fi
}

# Per case: origin, a shared clone and a worktree root. Sets
# CASE, BARE, SHARED, ROOT.
mk_case() { # <name>
  CASE="$TMP/$1"; BARE="$CASE/origin.git"; SHARED="$CASE/shared"; ROOT="$CASE/worktrees"
  mkdir -p "$CASE" "$ROOT" || die "mkdir $CASE failed"
  quiet "init origin" git init -q --bare -b main "$BARE"
  quiet "clone" git clone -q "$BARE" "$SHARED"
  quiet "switch main" git -C "$SHARED" switch -q -C main
  printf 'base\n' > "$SHARED/f" || die "write f failed"
  quiet "add f" git -C "$SHARED" add f
  quiet "commit base" git -C "$SHARED" commit -q -m base
  quiet "push main" git -C "$SHARED" push -q -u origin main
  quiet "set-head" git -C "$SHARED" remote set-head origin --auto >/dev/null
}

commit_file() { # <checkout> <file>
  printf '%s\n' "$2" > "$1/$2" || die "write $2 failed"
  quiet "add $2" git -C "$1" add "$2"
  quiet "commit $2" git -C "$1" commit -q -m "$2"
}

# Touch a worktree's files and its gitdir's activity files to <mtime>.
age_wt() { # <worktree> <mtime>
  local gitdir f
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir)" || die "rev-parse --absolute-git-dir failed in $1"
  for f in "$gitdir/HEAD" "$gitdir/index" "$gitdir/logs/HEAD"; do
    if [[ -e "$f" ]]; then touch -t "$2" "$f" || die "touch $f failed"; fi
  done
  find "$1" -path "$1/.git" -prune -o -exec touch -h -t "$2" {} + || die "touch the files of $1 failed"
}

# A branch on origin cut from main with one commit; merged into main or not.
push_branch() { # <branch> <merged 0|1>
  quiet "branch $1" git -C "$SHARED" switch -q -c "$1" main
  commit_file "$SHARED" "file-${1//\//-}"
  quiet "push $1" git -C "$SHARED" push -q origin "$1"
  quiet "switch main" git -C "$SHARED" switch -q main
  if (( $2 )); then
    quiet "merge $1" git -C "$SHARED" merge -q --ff-only "$1"
    quiet "push main" git -C "$SHARED" push -q origin main
  fi
  quiet "drop local $1" git -C "$SHARED" branch -q -D "$1"
}

on_origin() { git -C "$BARE" show-ref --verify --quiet "refs/heads/$1"; }
has_branch() { git -C "$SHARED" show-ref --verify --quiet "refs/heads/$1"; }

# Run the hook from <dir> with the case's environment; sets OUT, RC, ERR.
run_hook() { # <dir> [VAR=value...]
  local dir="$1"; shift
  RC=0
  OUT="$(cd "$dir" && env WORKTREE_ROOT="$ROOT" \
    PRUNE_NOW="$PRUNE_NOW" PRUNE_IDLE_HOURS=24 LEFTOVER_BUDGET_SEC=3600 "$@" \
    bash "$HOOK" </dev/null 2>"$CASE/hook.err")" || RC=$?
  ERR="$(cat "$CASE/hook.err")"
}

# The additionalContext text of OUT, or empty when OUT is empty.
context() {
  [[ -n "$OUT" ]] || return 0
  printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["additionalContext"])'
}

# A process with its cwd inside <dir>, registered with the fake lsof;
# returns once it is there. The FIFO read returns when the child writes after
# its cd: no polling, no deadline.
start_sleeper() { # <dir>
  local ready="$CASE/sleeper.ready" said
  mkfifo "$ready" || die "mkfifo $ready failed"
  (
    if cd "$1"; then
      printf 'in' > "$ready"
      exec sleep 3600
    fi
    printf 'cd-failed' > "$ready"
  ) &
  SLEEPER=$!
  said="$(cat "$ready")" || die "cannot read the sleeper's FIFO $ready"
  [[ "$said" == in ]] || die "the sleeper could not enter $1"
  printf '%s %s\n' "$SLEEPER" "$1" >> "$FAKE_LSOF_CWDS" || die "cannot register the sleeper with the fake lsof"
}
stop_sleeper() {
  if [[ -n "$SLEEPER" ]]; then
    if ! kill "$SLEEPER"; then echo "warn: could not stop sleeper $SLEEPER" >&2; fi
    # A killed sleeper exits non-zero: that is the expected outcome here.
    wait "$SLEEPER"
  fi
  SLEEPER=""
  if [[ -n "${FAKE_LSOF_CWDS:-}" && -e "${FAKE_LSOF_CWDS:-}" ]] && ! : > "$FAKE_LSOF_CWDS"; then
    echo "warn: could not clear the fake lsof's records" >&2
  fi
  return 0
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

# A staged plugin: the hook and the result-check module it imports, laid out
# as the plugin ships them. Each case adds its own owner script.
stage_plugin() { # <dir> [hooks-dir-name]
  local hooks="${2:-hooks}"
  mkdir -p "$1/$hooks" "$1/lib" || die "mkdir stage $1 failed"
  cp "$HOOK" "$1/$hooks/" || die "stage the hook failed"
  cp "${HERE}/../../skills/release/lib/prune_result.py" \
    "$1/lib/" || die "stage the result checks failed"
}

# A staged plugin with the real runner and an owner script that records its
# name and GIT_SSH_COMMAND to <calls>, then fails.
stage_recording_plugin() { # <dir> <calls>
  stage_plugin "$1"
  cp "${HERE}/../../skills/release/lib/bounded-run.sh" "$1/lib/" || die "stage the runner failed"
  # shellcheck disable=SC2016  # GIT_SSH_COMMAND expands in the stand-in, not here.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%%s %%s\\n" "prune-worktrees.sh" "${GIT_SSH_COMMAND:-}" >> %q\nexit 1\n' "$2" \
    > "$1/lib/prune-worktrees.sh" || die "write the recording owner script failed"
}

main() {
  command -v python3 >/dev/null || die "python3 is required"
  TMP="$(mktemp -d)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "cannot resolve $TMP"
  trap cleanup EXIT
  write_fake_lsof "$TMP/fake-lsof/lsof"
  export PRUNE_LSOF="$TMP/fake-lsof/lsof" FAKE_LSOF_CWDS="$TMP/fake-lsof/cwds"
  export HOME="$TMP/home" GIT_CONFIG_NOSYSTEM=1
  mkdir -p "$HOME" || die "mkdir HOME failed"
  export GIT_AUTHOR_NAME=Ada GIT_AUTHOR_EMAIL=ada@example.invalid
  export GIT_COMMITTER_NAME=Ada GIT_COMMITTER_EMAIL=ada@example.invalid
  export GIT_AUTHOR_DATE="$FIXTURE_DATE" GIT_COMMITTER_DATE="$FIXTURE_DATE"
  unset SESSION_START_MODE GIT_SSH_COMMAND
  local ctx

  echo "1. removable local worktrees and branches go, origin is unchanged, and nothing is printed"
  mk_case c1
  quiet "wt add" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  quiet "local branch" git -C "$SHARED" branch -q --no-track feat/local-merged main
  push_branch feat/remote-merged 1
  push_branch feat/remote-held 0
  quiet "origin-held local branch" git -C "$SHARED" branch -q --no-track feat/local-held origin/feat/remote-held
  local origin_before origin_after
  origin_before="$(git -C "$BARE" for-each-ref --format='%(refname) %(objectname)' refs/heads)" || die "read origin refs failed"
  run_hook "$SHARED"
  origin_after="$(git -C "$BARE" for-each-ref --format='%(refname) %(objectname)' refs/heads)" || die "read origin refs failed"
  if [[ $RC -eq 0 && -z "$OUT" && ! -e "$ROOT/spent" ]] && ! has_branch feat/local-merged \
     && ! has_branch feat/local-held && [[ "$origin_before" == "$origin_after" ]] \
     && on_origin feat/remote-merged && on_origin feat/remote-held && on_origin main; then pass
  else fail "c1: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "2. only work the operator must decide on is listed, and none of it is touched"
  mk_case c2
  quiet "wt dirty" git -C "$SHARED" worktree add -q -b review/dirty "$ROOT/dirty" origin/main
  printf 'notes\n' > "$ROOT/dirty/notes.txt" || die "write notes failed"
  age_wt "$ROOT/dirty" "$AGED_MTIME"
  quiet "wt unpushed" git -C "$SHARED" worktree add -q -b feat/unpushed "$ROOT/unpushed" origin/main
  commit_file "$ROOT/unpushed" u.txt
  age_wt "$ROOT/unpushed" "$AGED_MTIME"
  quiet "wt detached" git -C "$SHARED" worktree add -q --detach "$ROOT/detached" origin/main
  commit_file "$ROOT/detached" d.txt
  age_wt "$ROOT/detached" "$AGED_MTIME"
  quiet "local unpushed" git -C "$SHARED" switch -q -c feat/local-only main
  commit_file "$SHARED" l.txt
  quiet "switch main" git -C "$SHARED" switch -q main
  push_branch feat/stale 0
  local other="$CASE/other"
  quiet "clone other" git clone -q "$BARE" "$other"
  quiet "wt other" git -C "$other" worktree add -q -b review/other "$ROOT/other-dirty" origin/main
  printf 'x\n' > "$ROOT/other-dirty/x.txt" || die "write other failed"
  age_wt "$ROOT/other-dirty" "$AGED_MTIME"
  run_hook "$SHARED"
  ctx="$(context)"
  if [[ $RC -eq 0 ]] && [[ "$ctx" == "Session-start status — 4 item(s)"* ]] \
     && [[ "$ctx" == *"$ROOT/dirty (review/dirty): 1 uncommitted file(s)"* ]] \
     && [[ "$ctx" == *"$ROOT/unpushed (feat/unpushed): 1 commit(s) origin does not hold"* ]] \
     && [[ "$ctx" == *"local branch feat/local-only: 1 unpushed commit(s)"* ]] \
     && [[ "$ctx" == *"$ROOT/detached (detached): 1 commit(s) origin does not hold"* ]] \
     && [[ "$ctx" == *"Ask the user what to do with each item."* && "$ctx" != *"one at a time"* ]] \
     && [[ "$ctx" != *feat/stale* && "$ctx" != *other-dirty* ]] \
     && [[ -e "$ROOT/dirty/notes.txt" && -e "$ROOT/unpushed" && -e "$ROOT/detached" && -e "$ROOT/other-dirty" ]] \
     && has_branch feat/local-only && on_origin feat/stale; then pass
  else fail "c2: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "3. a dirty worktree inside the idle window is not listed"
  mk_case c3
  quiet "wt fresh" git -C "$SHARED" worktree add -q -b review/fresh "$ROOT/fresh" origin/main
  printf 'wip\n' > "$ROOT/fresh/wip.txt" || die "write wip failed"
  age_wt "$ROOT/fresh" "$FRESH_MTIME"
  run_hook "$SHARED"
  if [[ $RC -eq 0 && -z "$OUT" && -e "$ROOT/fresh/wip.txt" ]]; then pass
  else fail "c3: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "4. a process inside keeps a removable worktree, and it is not listed"
  mk_case c4
  quiet "wt busy" git -C "$SHARED" worktree add -q --detach "$ROOT/busy" origin/main
  age_wt "$ROOT/busy" "$AGED_MTIME"
  start_sleeper "$ROOT/busy"
  run_hook "$SHARED"
  stop_sleeper
  if [[ $RC -eq 0 && -z "$OUT" && -e "$ROOT/busy" ]]; then pass
  else fail "c4: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "6. a session in a linked worktree cleans the shared repository"
  mk_case c6
  quiet "wt session" git -C "$SHARED" worktree add -q -b review/session "$ROOT/session" origin/main
  quiet "wt spent" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  push_branch feat/remote-merged 1
  local real_hook6="$HOOK"
  HOOK="${HERE}/../../skills/release/hooks/session-start.sh"
  run_hook "$ROOT/session" SESSION_START_HOOKS=check-leftover-worktrees
  HOOK="$real_hook6"
  if [[ $RC -eq 0 && -z "$OUT" && ! -e "$ROOT/spent" && -e "$ROOT/session" ]] && on_origin feat/remote-merged; then pass
  else fail "c6: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "7. portable mode removes what natively goes and lists what awaits the operator"
  mk_case c7
  quiet "wt spent" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  quiet "wt dirty" git -C "$SHARED" worktree add -q -b review/dirty "$ROOT/dirty" origin/main
  printf 'notes\n' > "$ROOT/dirty/notes.txt" || die "write notes failed"
  age_wt "$ROOT/dirty" "$AGED_MTIME"
  push_branch feat/remote-merged 1
  run_hook "$SHARED" SESSION_START_MODE=portable
  ctx="$(context)"
  if [[ $RC -eq 0 && ! -e "$ROOT/spent" ]] && on_origin feat/remote-merged \
     && [[ "$ctx" == "Session-start status — 1 item(s)"*"$ROOT/dirty"* ]]; then pass
  else fail "c7: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "8. a repository without origin, and a directory outside any, are silent"
  mk_case c8
  quiet "remove origin" git -C "$SHARED" remote remove origin
  run_hook "$SHARED"
  local rc_a=$RC out_a=$OUT err_a=$ERR
  mkdir -p "$CASE/plain" || die "mkdir plain failed"
  run_hook "$CASE/plain" GIT_CEILING_DIRECTORIES="$CASE"
  if [[ $rc_a -eq 0 && -z "$out_a" && -z "$err_a" && $RC -eq 0 && -z "$OUT" && -z "$ERR" ]]; then pass
  else fail "c8: no-origin RC=$rc_a OUT=$out_a ERR=$err_a; plain RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "9. a run past the budget is a could-not-check line, never silence"
  mk_case c9
  quiet "wt spent" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  # A staged plugin whose bounded runner reports every command out of time:
  # the budget ends by the runner's own verdict, never by waiting on a clock.
  local stage="$CASE/stage"
  stage_plugin "$stage"
  cp "${HERE}/../../skills/release/lib/prune-worktrees.sh" \
    "$stage/lib/" || die "stage the owner script failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\necho "bounded-run: stand-in budget spent" >&2\nexit 124\n' > "$stage/lib/bounded-run.sh" \
    || die "write the stand-in runner failed"
  local real_hook="$HOOK"
  HOOK="$stage/hooks/$(basename "$real_hook")"
  run_hook "$SHARED"
  HOOK="$real_hook"
  ctx="$(context)"
  if [[ $RC -eq 0 && -e "$ROOT/spent" ]] && [[ "$ctx" == "Session-start status — could not check"*"time budget"* ]]; then pass
  else fail "c9: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "10. a missing git or python3 is a could-not-check status, never stderr alone"
  mk_case c10
  local bare_path="$CASE/path-nogit" real_bash
  real_bash="$(command -v bash)" || die "bash not found"
  mkdir -p "$bare_path" "$CASE/path-nopy" || die "mkdir the PATH dirs failed"
  ln -s "$(command -v git)" "$CASE/path-nopy/git" || die "link git failed"
  local ctx_git ctx_py
  RC=0; OUT="$(cd "$SHARED" && PATH="$bare_path" "$real_bash" "$HOOK" </dev/null 2>"$CASE/hook.err")" || RC=$?
  ctx_git="$(context)"
  local rc_git=$RC
  RC=0; OUT="$(cd "$SHARED" && PATH="$CASE/path-nopy" "$real_bash" "$HOOK" </dev/null 2>"$CASE/hook.err")" || RC=$?
  ctx_py="$(context)"
  if [[ $rc_git -eq 0 && $RC -eq 0 ]] \
     && [[ "$ctx_git" == "Session-start status — could not check this repository"*"git is not on PATH"* ]] \
     && [[ "$ctx_py" == "Session-start status — could not check this repository"*"python3 is not on PATH"* ]]; then pass
  else fail "c10: git=$ctx_git python3=$ctx_py"; fi

  echo "12. an owner result missing its documented fields is a could-not-check status, never a clean one"
  mk_case c12
  local stage12="$CASE/stage"
  stage_plugin "$stage12"
  cp "${HERE}/../../skills/release/lib/bounded-run.sh" "$stage12/lib/" || die "stage the runner failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "{}\\n"\n' > "$stage12/lib/prune-worktrees.sh" || die "write the stand-in prune failed"
  local real_hook12="$HOOK"
  HOOK="$stage12/hooks/$(basename "$real_hook12")"
  run_hook "$SHARED"
  HOOK="$real_hook12"
  ctx="$(context)"
  if [[ $RC -eq 0 ]] && [[ "$ctx" == "Session-start status — could not check this repository"* ]] \
     && [[ "$ctx" == *"prune-worktrees.sh exited 0 with a result holding no worktrees_removed list"* ]]; then pass
  else fail "c12: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "11. a shared checkout whose name ends in a newline is cleaned, never truncated"
  local nl=$'\n'
  if mkdir "$TMP/nl-probe${nl}" 2>"$TMP/nl.err"; then
    rmdir "$TMP/nl-probe${nl}" || die "rmdir the newline probe failed"
    mk_case c11
    local nl_shared="$CASE/shared-nl${nl}"
    quiet "clone newline" git clone -q "$BARE" "$nl_shared"
    quiet "set-head newline" git -C "$nl_shared" remote set-head origin --auto
    quiet "wt spent" git -C "$nl_shared" worktree add -q --detach "$ROOT/spent" origin/main
    age_wt "$ROOT/spent" "$AGED_MTIME"
    push_branch feat/remote-merged 1
    run_hook "$nl_shared"
    if [[ $RC -eq 0 && -z "$OUT" && ! -e "$ROOT/spent" ]] && on_origin feat/remote-merged; then pass
    else fail "c11: RC=$RC OUT=$OUT ERR=$ERR"; fi
  else
    echo "11. skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl.err"))"
  fi

  echo "14. a git worktree list that fails after a valid prefix does not run the owner script"
  mk_case c14
  local stage14="$CASE/stage14" calls14="$CASE/calls14" real_git
  stage_recording_plugin "$stage14" "$calls14"
  real_git="$(command -v git)" || die "git not found"
  mkdir -p "$CASE/failing-git" || die "mkdir failing-git failed"
  # Prints the real inventory, then fails: the prefix alone parses cleanly.
  # shellcheck disable=SC2016  # The $@ belongs to the stand-in git.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "${1:-}" == worktree && "${2:-}" == list ]]; then\n  %q "$@"\n  echo "fatal: stand-in failure after the listing" >&2\n  exit 1\nfi\nexec %q "$@"\n' \
    "$real_git" "$real_git" > "$CASE/failing-git/git" || die "write the failing git failed"
  chmod +x "$CASE/failing-git/git" || die "chmod the failing git failed"
  local real_hook14="$HOOK"
  HOOK="$stage14/hooks/$(basename "$real_hook14")"
  RC=0
  OUT="$(cd "$SHARED" && env PATH="$CASE/failing-git:$PATH" LEFTOVER_BUDGET_SEC=3600 \
    bash "$HOOK" </dev/null 2>"$CASE/hook.err")" || RC=$?
  HOOK="$real_hook14"
  ctx="$(context)"
  if [[ $RC -eq 0 && ! -e "$calls14" ]] \
     && [[ "$ctx" == "Session-start status — could not check this repository"*"git worktree list --porcelain -z"*"exited 1"* ]]; then pass
  else fail "c14: RC=$RC OUT=$OUT ERR=$(cat "$CASE/hook.err") calls=$(if [[ -e "$calls14" ]]; then cat "$calls14"; fi)"; fi

  echo "15. BatchMode reaches the owner script whether or not GIT_SSH_COMMAND is already set"
  mk_case c15
  local stage15="$CASE/stage15" calls15="$CASE/calls15" set_line unset_line
  stage_recording_plugin "$stage15" "$calls15"
  local real_hook15="$HOOK"
  HOOK="$stage15/hooks/$(basename "$real_hook15")"
  run_hook "$SHARED" GIT_SSH_COMMAND="ssh -i /keys/case15"
  set_line="$(head -n 1 "$calls15")"
  : > "$calls15" || die "clear calls15 failed"
  run_hook "$SHARED"
  unset_line="$(head -n 1 "$calls15")"
  HOOK="$real_hook15"
  if [[ "$set_line" == "prune-worktrees.sh ssh -i /keys/case15 -o BatchMode=yes" \
     && "$unset_line" == "prune-worktrees.sh ssh -o BatchMode=yes" ]]; then pass
  else fail "c15: set=$set_line unset=$unset_line ERR=$ERR"; fi

  echo "16. a hooks directory whose name ends in a newline still reaches its owner script"
  # A `$(dirname ...)` capture drops the newline, and the hook then looks for
  # its owner script beside a directory that does not exist (#487).
  if mkdir "$TMP/nl-probe16${nl}" 2>"$TMP/nl16.err"; then
    rmdir "$TMP/nl-probe16${nl}" || die "rmdir the newline probe failed"
    mk_case c16
    quiet "wt add" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
    age_wt "$ROOT/spent" "$AGED_MTIME"
    local stage16="$CASE/stage16"
    stage_plugin "$stage16" "hooks${nl}"
    cp "${HERE}/../../skills/release/lib/bounded-run.sh" "${HERE}/../../skills/release/lib/prune-worktrees.sh" \
      "$stage16/lib/" \
      || die "stage the owner script failed"
    local real_hook16="$HOOK"
    HOOK="$stage16/hooks${nl}/$(basename "$real_hook16")"
    run_hook "$SHARED"
    HOOK="$real_hook16"
    if [[ $RC -eq 0 && -z "$OUT" && ! -e "$ROOT/spent" ]]; then pass
    else fail "c16: RC=$RC OUT=$OUT ERR=$ERR"; fi
  else
    echo "16. skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl16.err"))"
  fi

  echo "17. a locked worktree stays with its lock holder, with or without a reason, and is not listed"
  # The unlocked twin's removal proves the prune ran on a removable shape (#831).
  mk_case c17
  quiet "wt held" git -C "$SHARED" worktree add -q -b held "$ROOT/held" origin/main
  quiet "wt bare" git -C "$SHARED" worktree add -q -b bare "$ROOT/bare" origin/main
  quiet "wt loose" git -C "$SHARED" worktree add -q --detach "$ROOT/loose" origin/main
  quiet "lock held" git -C "$SHARED" worktree lock --reason "factory task 831" "$ROOT/held"
  quiet "lock bare" git -C "$SHARED" worktree lock "$ROOT/bare"
  age_wt "$ROOT/held" "$AGED_MTIME"
  age_wt "$ROOT/bare" "$AGED_MTIME"
  age_wt "$ROOT/loose" "$AGED_MTIME"
  run_hook "$SHARED"
  local list17
  list17="$(git -C "$SHARED" worktree list --porcelain)" || die "git worktree list failed"
  if [[ $RC -eq 0 && -z "$OUT" && -d "$ROOT/held" && -d "$ROOT/bare" && ! -e "$ROOT/loose" ]] \
     && grep -qxF "locked factory task 831" <<<"$list17" && grep -qxF "locked" <<<"$list17" \
     && has_branch held && has_branch bare; then pass
  else fail "c17: RC=$RC OUT=$OUT LIST=$list17 ERR=$ERR"; fi

  # The hook is inert: its marker proves execution without any remote write.
  # A control ref update proves each fixture's hook is executable and active.
  local event hook_log configured_hooks native
  native="${HERE}/../../skills/release/native-hook.sh"
  for event in session-start stop-handoff; do
    echo "configured Git hooks do not run during ${event} cleanup"
    mk_case "git-hooks-${event}"
    quiet "local spent branch" git -C "$SHARED" branch spent main
    quiet "stage marker" git -C "$BARE" update-ref refs/heads/stage-marker main
    if [[ "$event" == session-start ]]; then
      quiet "spent worktree" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" main
      age_wt "$ROOT/spent" "$AGED_MTIME"
    fi
    configured_hooks="$CASE/repo-hooks"; hook_log="$CASE/hook-ran"
    mkdir -p "$configured_hooks" || die "mkdir hooks failed"
    cat > "$configured_hooks/reference-transaction" <<'SH' || die "write hook failed"
#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
if [[ "$1" == committed ]]; then
  printf 'reference hook ran\n' >> "${FIXTURE_HOOK_LOG:?}"
fi
SH
    chmod +x "$configured_hooks/reference-transaction" || die "chmod hook failed"
    quiet "configure hooks" git -C "$SHARED" config core.hooksPath "$configured_hooks"
    quiet "hook control" env FIXTURE_HOOK_LOG="$hook_log" git -C "$SHARED" update-ref refs/heads/probe main
    [[ -s "$hook_log" ]] || die "control did not execute the configured hook"
    quiet "remove probe" git -c core.hooksPath=/dev/null -C "$SHARED" update-ref -d refs/heads/probe
    rm "$hook_log" || die "remove hook marker failed"
    origin_before="$(git -C "$BARE" for-each-ref --format='%(refname) %(objectname)' refs/heads)" || die "read origin refs failed"
    RC=0
    OUT="$(cd "$SHARED" && env WORKTREE_ROOT="$ROOT" PRUNE_NOW="$PRUNE_NOW" \
      PRUNE_IDLE_HOURS=24 LEFTOVER_BUDGET_SEC=3600 FIXTURE_HOOK_LOG="$hook_log" \
      SESSION_START_HOOKS=check-leftover-worktrees \
      bash "$native" "$event" <<<'{}' 2>"$CASE/hook.err")" || RC=$?
    ERR="$(cat "$CASE/hook.err")"
    origin_after="$(git -C "$BARE" for-each-ref --format='%(refname) %(objectname)' refs/heads)" || die "read origin refs failed"
    if [[ $RC -ne 0 || -e "$hook_log" || "$origin_before" != "$origin_after" ]] \
       || [[ "$(git -C "$SHARED" config --get core.hooksPath)" != "$configured_hooks" ]]; then
      fail "${event}: RC=$RC OUT=$OUT ERR=$ERR"
    elif [[ "$event" == session-start ]]; then
      if [[ -z "$OUT" && ! -e "$ROOT/spent" ]] && ! has_branch spent; then pass
      else fail "session-start did not clean local work: OUT=$OUT ERR=$ERR"; fi
    else
      if has_branch spent && git -C "$SHARED" show-ref --verify --quiet refs/remotes/origin/stage-marker \
         && python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["decision"] == "block" and "branch spent" in d["reason"] else 1)' <<<"$OUT"; then pass
      else fail "stop did not fetch and preview local cleanup: OUT=$OUT ERR=$ERR"; fi
    fi
  done

  echo
  echo "passed=${PASS} failed=${FAIL}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
