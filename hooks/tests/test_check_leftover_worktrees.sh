#!/usr/bin/env bash
# The session-start half of the leftovers net.
#
# The predicate itself is covered by skills/release/tests/test_check_leftovers.sh;
# these checks cover what this hook adds — reporting only the abandoned verdict,
# staying silent otherwise, and never failing a session start whatever the
# detector does.
#
# `set -e` is dropped so every case runs and the suite reports an aggregate;
# each case captures its own status (rules/error-handling.md aggregate-reporting
# carve-out).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${HERE}/../check-leftover-worktrees.sh"
PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

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

# The hook resolves its detector as ../skills/release/check-leftovers.sh, so a
# copy is staged in that shape rather than the hook being taught a test path.
#
# Staged OUTSIDE the repository under test, which is both where an installed
# plugin actually lives and the only way these checks mean anything: staging it
# inside leaves fresh untracked files in the tree, and the detector reads the
# NEWEST write among changed paths, so every case would report age 0 and no
# case could ever reach the abandoned verdict.
# `mktemp -d` rather than a counter: this runs inside $(...), a subshell, so an
# incremented global would be discarded and every case would reuse one stage —
# inheriting whichever detector an earlier case put there.
stage_hook() { # <detector-source-or-empty>
  local src="${1:-}" root
  root="$(mktemp -d "$TMP/stage.XXXXXX")" || die "mktemp stage"
  mkdir -p "$root/hooks" "$root/skills/release" || die "mkdir"
  cp "$HOOK" "$root/hooks/" || die "cp hook"
  if [ -n "$src" ]; then cp "$src" "$root/skills/release/check-leftovers.sh" || die "cp detector"; fi
  printf '%s\n' "$root/hooks/$(basename "$HOOK")"
}

run_hook() { # <cwd> <hook-path>
  OUT="$(cd "$1" && bash "$2" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/leftoverhook.XXXXXX")" || die "mktemp"
  ERRFILE="$TMP/err"
  cleanup() { rm -rf "$TMP"; return 0; }
  trap cleanup EXIT

  local real="${HERE}/../../skills/release/check-leftovers.sh"
  [ -r "$real" ] || die "detector not found beside the hook at $real"

  echo "▶ what it reports" >&2

  # An abandoned OTHER worktree: the nine-day shape.
  new_repo "$TMP/aband"
  git -C "$TMP/aband" worktree add -q -b stale "$TMP/aband-wt" HEAD || die "worktree add"
  echo lost >> "$TMP/aband-wt/file.txt"
  touch -t 202601010000 "$TMP/aband-wt/file.txt"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/aband" "$HOOKPATH"
  if [[ $RC -eq 0 ]] \
     && printf '%s' "$OUT" | grep -qF 'Session-start status — ' \
     && printf '%s' "$OUT" | grep -qF 'aband-wt'; then
    pass; else fail "an abandoned worktree is reported with the marker, got RC=$RC OUT=$OUT"; fi

  # Work in progress is what a session is for.
  new_repo "$TMP/busy"
  git -C "$TMP/busy" worktree add -q -b busy "$TMP/busy-wt" HEAD || die "worktree add"
  echo work >> "$TMP/busy-wt/file.txt"
  git -C "$TMP/busy-wt" commit -q -am progress || die "commit"
  echo more >> "$TMP/busy-wt/file.txt"
  touch -t 202601010000 "$TMP/busy-wt/file.txt"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/busy" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "a branch with its own commits is not reported, got RC=$RC OUT=$OUT"; fi

  # Dirt in the session's own worktree, aged, on a never-committed branch.
  new_repo "$TMP/selfaband"
  echo lost >> "$TMP/selfaband/file.txt"
  touch -t 202601010000 "$TMP/selfaband/file.txt"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/selfaband" "$HOOKPATH"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | grep -qF '(this session)'; then
    pass; else fail "an abandoned self worktree is marked as this session, got RC=$RC OUT=$OUT"; fi

  echo "▶ when it stays quiet" >&2

  new_repo "$TMP/clean"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/clean" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "a clean repo emits nothing, got RC=$RC OUT=$OUT"; fi

  mkdir -p "$TMP/notrepo"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/notrepo" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "a non-repository is a silent no-op, got RC=$RC OUT=$OUT"; fi

  echo "▶ it never fails a session start" >&2

  # Detector absent: warn on stderr, emit nothing, still exit 0.
  new_repo "$TMP/nodet"
  HOOKPATH="$(stage_hook "")"
  run_hook "$TMP/nodet" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -qF 'detector not readable'; then
    pass; else fail "an absent detector warns and no-ops, got RC=$RC ERR=$ERRTEXT"; fi

  # Detector in tool-error (rc 2): not a finding, so nothing is reported.
  new_repo "$TMP/broken"
  HOOKPATH="$(stage_hook "$real")"
  printf '#!/bin/sh\necho "{}"\nexit 2\n' > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
  run_hook "$TMP/broken" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -qF 'could not read'; then
    pass; else fail "a detector tool-error warns and no-ops, got RC=$RC ERR=$ERRTEXT"; fi

  # Unparseable detector output must not crash session start.
  new_repo "$TMP/garbage"
  HOOKPATH="$(stage_hook "$real")"
  printf '#!/bin/sh\necho "not json"\nexit 1\n' > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
  run_hook "$TMP/garbage" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "unparseable detector output is a silent no-op, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
