#!/usr/bin/env bash
# The gate that decides whether a release starts at all.
#
# Each case builds a throwaway repository with real worktrees, since the script
# reads `git worktree list`, `merge-base --is-ancestor` and file mtimes -- all
# of which a stub would have to reimplement, and a reimplementation is what
# these checks exist to catch drifting.
#
# `set -e` is dropped so every case runs and the suite reports an aggregate;
# each case captures its own status (rules/error-handling.md aggregate-reporting
# carve-out).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_UNDER_TEST="${HERE}/../check-leftovers.sh"
PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

# A repository with `main`, one commit, and an `origin/main` the script can
# resolve without a network: a second clone-free remote ref set by hand.
new_repo() { # <dir>
  local d="$1"
  mkdir -p "$d" || die "mkdir $d"
  git -C "$d" init -q -b main || die "git init"
  git -C "$d" config user.email t@example.com
  git -C "$d" config user.name Tester
  echo base > "$d/file.txt"
  git -C "$d" add file.txt || die "git add"
  git -C "$d" commit -q -m base || die "git commit"
  git -C "$d" update-ref refs/remotes/origin/main HEAD || die "update-ref"
}

run_check() { # <repo-dir> [env assignments...]
  OUT="$(cd "$1" && shift; bash "$SCRIPT_UNDER_TEST" --repo . 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

verdict_for() { # <json> <worktree-path-fragment>
  printf '%s' "$1" | python3 -c '
import json,sys
doc=json.load(sys.stdin); frag=sys.argv[1]
for row in doc["others"]:
    if frag in row["path"]:
        print(row["verdict"]); break
else:
    print("absent")
' "$2"
}

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/leftovers.XXXXXX")" || die "mktemp"
  ERRFILE="$TMP/err"
  cleanup() { rm -rf "$TMP"; return 0; }
  trap cleanup EXIT

  echo "▶ the releasing worktree" >&2

  new_repo "$TMP/clean"
  run_check "$TMP/clean"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | grep -qF '"ok":true'; then
    pass; else fail "a clean repo with no other worktrees exits 0, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/unstaged"; echo edit >> "$TMP/unstaged/file.txt"
  run_check "$TMP/unstaged"
  if [[ $RC -eq 1 ]] && printf '%s' "$ERRTEXT" | grep -qF 'publishes only what is committed'; then
    pass; else fail "an unstaged edit blocks, got RC=$RC ERR=$ERRTEXT"; fi

  new_repo "$TMP/staged"; echo edit >> "$TMP/staged/file.txt"; git -C "$TMP/staged" add file.txt
  run_check "$TMP/staged"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"staged":1'; then
    pass; else fail "a staged edit blocks and is counted, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/untracked"; echo scratch > "$TMP/untracked/leftover.txt"
  run_check "$TMP/untracked"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"untracked":1'; then
    pass; else fail "an untracked file blocks and is counted, got RC=$RC OUT=$OUT"; fi

  # Junk the repo already ignores is not the caller's problem.
  new_repo "$TMP/ignored"
  printf 'tester-data/\n' > "$TMP/ignored/.gitignore"
  git -C "$TMP/ignored" add .gitignore && git -C "$TMP/ignored" commit -q -m ignore
  mkdir -p "$TMP/ignored/tester-data" && echo out > "$TMP/ignored/tester-data/probe.json"
  run_check "$TMP/ignored"
  if [[ $RC -eq 0 ]]; then pass; else fail "a gitignored path does not block, got RC=$RC OUT=$OUT"; fi

  # The releasing worktree gets the same verdict the others do, for a caller
  # that wants only the abandoned shape. The gate itself ignores it.
  new_repo "$TMP/selfverdict"
  echo lost >> "$TMP/selfverdict/file.txt"
  touch -t 202601010000 "$TMP/selfverdict/file.txt"
  run_check "$TMP/selfverdict"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"abandoned"'; then
    pass; else fail "an aged self leftover on a never-committed branch reads abandoned, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/selffresh"; echo typing >> "$TMP/selffresh/file.txt"
  run_check "$TMP/selffresh"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"in_progress"'; then
    pass; else fail "fresh self dirt still blocks but reads in_progress, got RC=$RC OUT=$OUT"; fi

  run_check "$TMP/clean"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"clean"'; then
    pass; else fail "a clean self reads clean, got RC=$RC OUT=$OUT"; fi

  echo "▶ every other worktree" >&2

  # The pane-model shape: dirty, on a branch whose tip is already in main, aged.
  new_repo "$TMP/aband"
  git -C "$TMP/aband" worktree add -q -b stale "$TMP/aband-wt" HEAD || die "worktree add"
  echo lost >> "$TMP/aband-wt/file.txt"
  touch -t 202601010000 "$TMP/aband-wt/file.txt"
  run_check "$TMP/aband"
  if [[ $RC -eq 1 ]] \
     && [[ "$(verdict_for "$OUT" aband-wt)" == "abandoned" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'carrying no commits of its own'; then
    pass; else fail "an aged leftover on a never-committed branch blocks, got RC=$RC OUT=$OUT"; fi

  # Same shape, but the branch carries its own commit: git preserves it.
  new_repo "$TMP/ahead"
  git -C "$TMP/ahead" worktree add -q -b busy "$TMP/ahead-wt" HEAD || die "worktree add"
  echo work >> "$TMP/ahead-wt/file.txt"
  git -C "$TMP/ahead-wt" commit -q -am progress || die "commit in worktree"
  echo more >> "$TMP/ahead-wt/file.txt"
  touch -t 202601010000 "$TMP/ahead-wt/file.txt"
  run_check "$TMP/ahead"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" ahead-wt)" == "in_progress" ]]; then
    pass; else fail "a branch with its own commits does not block, got RC=$RC OUT=$OUT"; fi

  # Same shape, but written moments ago: someone is still typing.
  new_repo "$TMP/fresh"
  git -C "$TMP/fresh" worktree add -q -b justnow "$TMP/fresh-wt" HEAD || die "worktree add"
  echo typing >> "$TMP/fresh-wt/file.txt"
  run_check "$TMP/fresh"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" fresh-wt)" == "in_progress" ]]; then
    pass; else fail "a leftover younger than the age floor does not block, got RC=$RC OUT=$OUT"; fi

  # The age floor is the caller's to move.
  LEFTOVERS_MIN_AGE_HOURS=0
  export LEFTOVERS_MIN_AGE_HOURS
  run_check "$TMP/fresh"
  unset LEFTOVERS_MIN_AGE_HOURS
  if [[ $RC -eq 1 ]] && [[ "$(verdict_for "$OUT" fresh-wt)" == "abandoned" ]]; then
    pass; else fail "LEFTOVERS_MIN_AGE_HOURS=0 blocks the fresh leftover, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/otherclean"
  git -C "$TMP/otherclean" worktree add -q -b tidy "$TMP/otherclean-wt" HEAD || die "worktree add"
  run_check "$TMP/otherclean"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" otherclean-wt)" == "absent" ]]; then
    pass; else fail "a clean other worktree is not reported, got RC=$RC OUT=$OUT"; fi

  echo "▶ usage and tool state" >&2

  mkdir -p "$TMP/notrepo"
  run_check "$TMP/notrepo"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -qF 'not a git repository'; then
    pass; else fail "a non-repository exits 2, got RC=$RC ERR=$ERRTEXT"; fi

  OUT="$(bash "$SCRIPT_UNDER_TEST" --bogus 2>"$ERRFILE")"; RC=$?
  if [[ $RC -eq 2 ]] && printf '%s' "$OUT" | grep -qF '"ok":false'; then
    pass; else fail "an unknown flag exits 2 with a parseable envelope, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
