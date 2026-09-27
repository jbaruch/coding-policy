#!/usr/bin/env bash
# Outcome-based tests for hooks/check-leftover-worktrees.sh.
#
# Each case builds its own bare "origin", a shared clone, its own worktree
# root and a fake `gh` first on PATH that answers from files in the case's
# directory, so no case sees another's state and they run in any order
# (rules/testing-standards.md Independence). Every commit and reflog entry is
# dated FIXTURE_DATE, every aged file is touched to a fixed mtime, and the
# owner scripts judge at PRUNE_NOW, never the wall clock (Determinism).
#
# The owner scripts' decisions have their own suites
# (skills/herdr-foreman/tests/test_prune_worktrees.sh,
# test_prune_remote_branches.sh); these cases cover what the hook adds: it
# runs them live for this repository, lists only what the operator must
# decide, never acts in a worker session or in portable mode, and turns a
# failure into one "could not check" line.
#
# Covers:
#   1. Removable            -> an idle clean worktree origin holds, a merged
#                              local branch and a merged branch on origin are
#                              removed; nothing is printed.
#   2. Questionable         -> an idle dirty worktree, an idle unpushed
#                              worktree, an unpushed local branch and a stale
#                              unmerged branch on origin are listed and left.
#                              An open pull request's branch and another
#                              repository's dirty worktree are not listed.
#   3. Not yet idle         -> a fresh dirty worktree is not listed.
#   4. In use               -> a process inside keeps a removable worktree,
#                              unlisted.
#   5. gh failing           -> nothing on origin is deleted; one could-not-check
#                              line, never the URL gh printed.
#   6. Worker session       -> HERDR_ENV in a linked worktree: nothing deleted,
#                              nothing printed.
#   7. Portable mode        -> nothing deleted; the questionable list still
#                              printed.
#   8. No origin, no repo   -> silent.
#   9. Out of time          -> a could-not-check line naming the budget.
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash hooks/tests/test_check_leftover_worktrees.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${HERE}/../check-leftover-worktrees.sh"
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

# A fake gh answering from $CASE: `prs` lists open pull-request heads; `fail`
# makes every call exit 1 with a credential-bearing message.
write_fake_gh() {
  mkdir -p "$CASE/bin" || die "mkdir $CASE/bin failed"
  cat > "$CASE/bin/gh" <<'SH' || die "cannot write the fake gh"
#!/usr/bin/env bash
set -euo pipefail
d="${FAKE_GH_DIR:?}"
if [[ -e "$d/fail" ]]; then echo "gh: HTTP 502 from https://s3cr3t@example.invalid" >&2; exit 1; fi
case "$1" in
  api) exit 0 ;;
  pr)
    head=""
    while (( $# )); do
      if [[ "$1" == --head ]]; then head="$2"; shift; fi
      shift
    done
    [[ -f "$d/prs" ]] || exit 0
    if [[ -n "$head" ]]; then grep -xF -- "$head" "$d/prs" || [[ $? -eq 1 ]]; else cat "$d/prs"; fi
    exit 0 ;;
esac
echo "fake gh: unexpected call $*" >&2
exit 3
SH
  chmod +x "$CASE/bin/gh" || die "chmod the fake gh failed"
}

# Per case: origin, a shared clone, a worktree root and the fake gh. Sets
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
  write_fake_gh
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
  OUT="$(cd "$dir" && env PATH="$CASE/bin:$PATH" FAKE_GH_DIR="$CASE" WORKTREE_ROOT="$ROOT" \
    PRUNE_NOW="$PRUNE_NOW" PRUNE_IDLE_HOURS=24 PRUNE_REMOTE_IDLE_HOURS=24 "$@" \
    bash "$HOOK" </dev/null 2>"$CASE/hook.err")" || RC=$?
  ERR="$(cat "$CASE/hook.err")"
}

# The additionalContext text of OUT, or empty when OUT is empty.
context() {
  [[ -n "$OUT" ]] || return 0
  printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["additionalContext"])'
}

# A process with its cwd inside <dir>; returns once it is there.
start_sleeper() { # <dir>
  local ready="$CASE/sleeper.ready" tries=0
  (cd "$1" && : > "$ready" && exec sleep 600) &
  SLEEPER=$!
  while [[ ! -e "$ready" ]]; do
    (( tries++ < 100 )) || die "the sleeper never became ready"
    sleep 0.1
  done
}
stop_sleeper() {
  if [[ -n "$SLEEPER" ]]; then
    if ! kill "$SLEEPER"; then echo "warn: could not stop sleeper $SLEEPER" >&2; fi
    # A killed sleeper exits non-zero: that is the expected outcome here.
    wait "$SLEEPER"
  fi
  SLEEPER=""
  return 0
}

main() {
  command -v python3 >/dev/null || die "python3 is required"
  command -v lsof >/dev/null || die "lsof is required: the owner script's in-use probe runs it"
  TMP="$(mktemp -d)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "cannot resolve $TMP"
  trap cleanup EXIT
  export HOME="$TMP/home" GIT_CONFIG_NOSYSTEM=1
  mkdir -p "$HOME" || die "mkdir HOME failed"
  export GIT_AUTHOR_NAME=Ada GIT_AUTHOR_EMAIL=ada@example.invalid
  export GIT_COMMITTER_NAME=Ada GIT_COMMITTER_EMAIL=ada@example.invalid
  export GIT_AUTHOR_DATE="$FIXTURE_DATE" GIT_COMMITTER_DATE="$FIXTURE_DATE"
  unset HERDR_ENV SESSION_START_MODE
  local ctx

  echo "1. removable worktrees and branches go, and nothing is printed"
  mk_case c1
  quiet "wt add" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  quiet "local branch" git -C "$SHARED" branch -q --no-track feat/local-merged main
  push_branch feat/remote-merged 1
  run_hook "$SHARED"
  if [[ $RC -eq 0 && -z "$OUT" && ! -e "$ROOT/spent" ]] && ! has_branch feat/local-merged \
     && ! on_origin feat/remote-merged && on_origin main; then pass
  else fail "c1: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "2. only work the operator must decide on is listed, and none of it is touched"
  mk_case c2
  quiet "wt dirty" git -C "$SHARED" worktree add -q -b review/dirty "$ROOT/dirty" origin/main
  printf 'notes\n' > "$ROOT/dirty/notes.txt" || die "write notes failed"
  age_wt "$ROOT/dirty" "$AGED_MTIME"
  quiet "wt unpushed" git -C "$SHARED" worktree add -q -b feat/unpushed "$ROOT/unpushed" origin/main
  commit_file "$ROOT/unpushed" u.txt
  age_wt "$ROOT/unpushed" "$AGED_MTIME"
  quiet "local unpushed" git -C "$SHARED" switch -q -c feat/local-only main
  commit_file "$SHARED" l.txt
  quiet "switch main" git -C "$SHARED" switch -q main
  push_branch feat/stale 0
  push_branch feat/open-pr 0
  printf 'feat/open-pr\n' > "$CASE/prs" || die "write prs failed"
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
     && [[ "$ctx" == *"origin branch feat/stale: 1 commit(s) not in main"*"by Ada"* ]] \
     && [[ "$ctx" == *"one at a time"* ]] \
     && [[ "$ctx" != *feat/open-pr* && "$ctx" != *other-dirty* ]] \
     && [[ -e "$ROOT/dirty/notes.txt" && -e "$ROOT/unpushed" && -e "$ROOT/other-dirty" ]] \
     && has_branch feat/local-only && on_origin feat/stale && on_origin feat/open-pr; then pass
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

  echo "5. a failing gh deletes nothing on origin and says so in one line"
  mk_case c5
  push_branch feat/remote-merged 1
  : > "$CASE/fail" || die "write fail flag failed"
  run_hook "$SHARED"
  ctx="$(context)"
  if [[ $RC -eq 0 ]] && on_origin feat/remote-merged \
     && [[ "$ctx" == "Session-start status — could not check this repository"*"prune-remote-branches.sh"* ]] \
     && [[ "$ctx" != *$'\n'* && "$OUT$ERR" != *s3cr3t* ]]; then pass
  else fail "c5: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "6. a Herdr worker session deletes nothing and prints nothing"
  mk_case c6
  quiet "wt worker" git -C "$SHARED" worktree add -q -b review/worker "$ROOT/worker" origin/main
  quiet "wt spent" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  push_branch feat/remote-merged 1
  run_hook "$ROOT/worker" HERDR_ENV=1
  if [[ $RC -eq 0 && -z "$OUT" && -e "$ROOT/spent" ]] && on_origin feat/remote-merged; then pass
  else fail "c6: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "7. portable mode deletes nothing and still lists what awaits the operator"
  mk_case c7
  quiet "wt spent" git -C "$SHARED" worktree add -q --detach "$ROOT/spent" origin/main
  age_wt "$ROOT/spent" "$AGED_MTIME"
  quiet "wt dirty" git -C "$SHARED" worktree add -q -b review/dirty "$ROOT/dirty" origin/main
  printf 'notes\n' > "$ROOT/dirty/notes.txt" || die "write notes failed"
  age_wt "$ROOT/dirty" "$AGED_MTIME"
  push_branch feat/remote-merged 1
  run_hook "$SHARED" SESSION_START_MODE=portable
  ctx="$(context)"
  if [[ $RC -eq 0 && -e "$ROOT/spent" ]] && on_origin feat/remote-merged \
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
  printf '#!/usr/bin/env bash\nset -euo pipefail\nexec sleep 30\n' > "$CASE/slow-lsof" || die "write slow lsof failed"
  chmod +x "$CASE/slow-lsof" || die "chmod slow lsof failed"
  run_hook "$SHARED" LEFTOVER_BUDGET_SEC=2 PRUNE_LSOF="$CASE/slow-lsof"
  ctx="$(context)"
  if [[ $RC -eq 0 && -e "$ROOT/spent" ]] && [[ "$ctx" == "Session-start status — could not check"*"time budget"* ]]; then pass
  else fail "c9: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo
  echo "passed=${PASS} failed=${FAIL}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
