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

# Every fixture's mtime is this fixed past literal, so no case's verdict moves
# with the run-time clock (rules/testing-standards.md Determinism). The two
# verdicts are then selected by the age floor rather than by how old the file
# happens to be: AGED reads it as abandoned, FRESH is larger than any age the
# literal can reach and reads it as work in progress.
PINNED_MTIME=202601010000
AGED_FLOOR=1
FRESH_FLOOR=99999999

age() { touch -t "$PINNED_MTIME" "$@" || die "touch $*"; }

run_check() { # <repo-dir> <min-age-hours>
  OUT="$(cd "$1" && LEFTOVERS_MIN_AGE_HOURS="$2" bash "$SCRIPT_UNDER_TEST" --repo . 2>"$ERRFILE")"
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
  run_check "$TMP/clean" "$AGED_FLOOR"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | grep -qF '"ok":true'; then
    pass; else fail "a clean repo with no other worktrees exits 0, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/unstaged"; echo edit >> "$TMP/unstaged/file.txt"; age "$TMP/unstaged/file.txt"
  run_check "$TMP/unstaged" "$AGED_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$ERRTEXT" | grep -qF 'publishes only what is committed'; then
    pass; else fail "an unstaged edit blocks, got RC=$RC ERR=$ERRTEXT"; fi

  new_repo "$TMP/staged"; echo edit >> "$TMP/staged/file.txt"; git -C "$TMP/staged" add file.txt
  age "$TMP/staged/file.txt"
  run_check "$TMP/staged" "$AGED_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"staged":1'; then
    pass; else fail "a staged edit blocks and is counted, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/untracked"; echo scratch > "$TMP/untracked/leftover.txt"
  age "$TMP/untracked/leftover.txt"
  run_check "$TMP/untracked" "$AGED_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"untracked":1'; then
    pass; else fail "an untracked file blocks and is counted, got RC=$RC OUT=$OUT"; fi

  # Junk the repo already ignores is not the caller's problem.
  new_repo "$TMP/ignored"
  printf 'tester-data/\n' > "$TMP/ignored/.gitignore"
  git -C "$TMP/ignored" add .gitignore && git -C "$TMP/ignored" commit -q -m ignore
  mkdir -p "$TMP/ignored/tester-data" && echo out > "$TMP/ignored/tester-data/probe.json"
  age "$TMP/ignored/tester-data/probe.json"
  run_check "$TMP/ignored" "$AGED_FLOOR"
  if [[ $RC -eq 0 ]]; then pass; else fail "a gitignored path does not block, got RC=$RC OUT=$OUT"; fi

  # The releasing worktree gets the same verdict the others do, for a caller
  # that wants only the abandoned shape. The gate itself ignores it.
  new_repo "$TMP/selfverdict"
  echo lost >> "$TMP/selfverdict/file.txt"
  age "$TMP/selfverdict/file.txt"
  run_check "$TMP/selfverdict" "$AGED_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"abandoned"'; then
    pass; else fail "an aged self leftover on a never-committed branch reads abandoned, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/selffresh"; echo typing >> "$TMP/selffresh/file.txt"
  age "$TMP/selffresh/file.txt"
  run_check "$TMP/selffresh" "$FRESH_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"in_progress"'; then
    pass; else fail "fresh self dirt still blocks but reads in_progress, got RC=$RC OUT=$OUT"; fi

  run_check "$TMP/clean" "$AGED_FLOOR"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | grep -qF '"verdict":"clean"'; then
    pass; else fail "a clean self reads clean, got RC=$RC OUT=$OUT"; fi

  echo "▶ every other worktree" >&2

  # The pane-model shape: dirty, on a branch whose tip is already in main, aged.
  new_repo "$TMP/aband"
  git -C "$TMP/aband" worktree add -q -b stale "$TMP/aband-wt" HEAD || die "worktree add"
  echo lost >> "$TMP/aband-wt/file.txt"
  age "$TMP/aband-wt/file.txt"
  run_check "$TMP/aband" "$AGED_FLOOR"
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
  age "$TMP/ahead-wt/file.txt"
  run_check "$TMP/ahead" "$AGED_FLOOR"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" ahead-wt)" == "in_progress" ]]; then
    pass; else fail "a branch with its own commits does not block, got RC=$RC OUT=$OUT"; fi

  # Same shape, but younger than the floor: someone is still typing.
  new_repo "$TMP/fresh"
  git -C "$TMP/fresh" worktree add -q -b justnow "$TMP/fresh-wt" HEAD || die "worktree add"
  echo typing >> "$TMP/fresh-wt/file.txt"
  age "$TMP/fresh-wt/file.txt"
  run_check "$TMP/fresh" "$FRESH_FLOOR"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" fresh-wt)" == "in_progress" ]]; then
    pass; else fail "a leftover younger than the age floor does not block, got RC=$RC OUT=$OUT"; fi

  # The same fixture, same mtime: only the floor moved, and the verdict flips.
  run_check "$TMP/fresh" 0
  if [[ $RC -eq 1 ]] && [[ "$(verdict_for "$OUT" fresh-wt)" == "abandoned" ]]; then
    pass; else fail "LEFTOVERS_MIN_AGE_HOURS=0 blocks the same leftover, got RC=$RC OUT=$OUT"; fi

  new_repo "$TMP/otherclean"
  git -C "$TMP/otherclean" worktree add -q -b tidy "$TMP/otherclean-wt" HEAD || die "worktree add"
  run_check "$TMP/otherclean" "$AGED_FLOOR"
  if [[ $RC -eq 0 ]] && [[ "$(verdict_for "$OUT" otherclean-wt)" == "absent" ]]; then
    pass; else fail "a clean other worktree is not reported, got RC=$RC OUT=$OUT"; fi

  echo "▶ usage and tool state" >&2

  mkdir -p "$TMP/notrepo"
  run_check "$TMP/notrepo" "$AGED_FLOOR"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -qF 'not a git repository'; then
    pass; else fail "a non-repository exits 2, got RC=$RC ERR=$ERRTEXT"; fi

  OUT="$(bash "$SCRIPT_UNDER_TEST" --bogus 2>"$ERRFILE")"; RC=$?
  if [[ $RC -eq 2 ]] && printf '%s' "$OUT" | grep -qF '"ok":false'; then
    pass; else fail "an unknown flag exits 2 with a parseable envelope, got RC=$RC OUT=$OUT"; fi

  # A floor that is not a whole number of hours would make every -ge comparison
  # error, and a failed comparison reads as false -- the verdict that spares the
  # worktree. It is refused up front instead.
  new_repo "$TMP/badfloor"
  run_check "$TMP/badfloor" "four"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -qF 'LEFTOVERS_MIN_AGE_HOURS'; then
    pass; else fail "a non-numeric age floor exits 2, got RC=$RC ERR=$ERRTEXT"; fi

  run_check "$TMP/badfloor" "-1"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -qF 'LEFTOVERS_MIN_AGE_HOURS'; then
    pass; else fail "a negative age floor exits 2, got RC=$RC ERR=$ERRTEXT"; fi

  # `rev-parse --abbrev-ref HEAD` prints HEAD and exits 0 on a detached HEAD, so
  # that case is a branch name to translate, not a failure to absorb.
  new_repo "$TMP/detached"
  git -C "$TMP/detached" checkout -q --detach HEAD || die "detach"
  echo lost >> "$TMP/detached/file.txt"
  age "$TMP/detached/file.txt"
  run_check "$TMP/detached" "$AGED_FLOOR"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -qF '"branch":"DETACHED"'; then
    pass; else fail "a detached HEAD reads DETACHED, got RC=$RC OUT=$OUT"; fi

  # An unreadable worktree is a tool error, never the "clean" it would otherwise
  # be indistinguishable from.
  new_repo "$TMP/unreadable"
  git -C "$TMP/unreadable" worktree add -q -b broken "$TMP/unreadable-wt" HEAD || die "worktree add"
  echo lost >> "$TMP/unreadable-wt/file.txt"
  age "$TMP/unreadable-wt/file.txt"
  printf 'gitdir: /nonexistent/never/here\n' > "$TMP/unreadable-wt/.git" || die "corrupt gitdir"
  run_check "$TMP/unreadable" "$AGED_FLOOR"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -qF 'unreadable-wt'; then
    pass; else fail "a worktree git cannot read exits 2, got RC=$RC ERR=$ERRTEXT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
