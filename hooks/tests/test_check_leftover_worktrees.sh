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

# Every fixture's mtime is this fixed past literal and the age floor is passed
# explicitly, so no case's verdict moves with the run-time clock
# (rules/testing-standards.md Determinism).
PINNED_MTIME=202601010000
AGED_FLOOR=1

age() { touch -t "$PINNED_MTIME" "$@" || die "touch $*"; }

# A frozen "now" for every case that reads an age. Fixture mtimes alone leave
# the other half of the subtraction on the runtime clock, and Determinism asks
# for the clock itself to be injected (rules/testing-standards.md). The detector
# calls `date +%s` and nothing else, so the shim answers that and fails loudly on
# anything else rather than letting a changed call quietly reach the real clock.
FROZEN_NOW=1780185600
# TZ is pinned with it: `touch -t` reads local time, so without this the age is
# the frozen clock minus a mtime that moves with the runner's zone. Pinned, the
# gap between FROZEN_NOW and PINNED_MTIME is the same everywhere, and
# skills/release/tests/test_check_leftovers.sh asserts that constant outright.
export TZ=UTC

freeze_clock() { # <bin-dir>
  mkdir -p "$1" || die "mkdir $1"
  {
    printf '#!/bin/sh\n'
    # shellcheck disable=SC2016  # The single quotes are the point: $1 and $*
    # belong to the shim being written, not to this shell.
    printf 'if [ "$1" = "+%%s" ]; then echo %s; exit 0; fi\n' "$FROZEN_NOW"
    printf 'echo "frozen-clock shim: unexpected date invocation: $*" >&2\n'
    printf 'exit 2\n'
  } > "$1/date" || die "write clock shim"
  chmod +x "$1/date" || die "chmod clock shim"
}

run_hook() { # <cwd> <hook-path>
  OUT="$(cd "$1" && PATH="$CLOCKBIN:$PATH" LEFTOVERS_MIN_AGE_HOURS="$AGED_FLOOR" \
    bash "$2" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/leftoverhook.XXXXXX")" || die "mktemp"
  ERRFILE="$TMP/err"
  cleanup() { rm -rf "$TMP"; return 0; }
  trap cleanup EXIT
  CLOCKBIN="$TMP/clockbin"
  freeze_clock "$CLOCKBIN"

  local real="${HERE}/../../skills/release/check-leftovers.sh"
  [ -r "$real" ] || die "detector not found beside the hook at $real"

  echo "▶ what it reports" >&2

  # An abandoned OTHER worktree: the nine-day shape.
  new_repo "$TMP/aband"
  git -C "$TMP/aband" worktree add -q -b stale "$TMP/aband-wt" HEAD || die "worktree add"
  echo lost >> "$TMP/aband-wt/file.txt"
  age "$TMP/aband-wt/file.txt"
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
  age "$TMP/busy-wt/file.txt"
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/busy" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "a branch with its own commits is not reported, got RC=$RC OUT=$OUT"; fi

  # Dirt in the session's own worktree, aged, on a never-committed branch.
  new_repo "$TMP/selfaband"
  echo lost >> "$TMP/selfaband/file.txt"
  age "$TMP/selfaband/file.txt"
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
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'exited 2 instead of reporting a verdict'; then
    pass; else fail "a detector tool-error warns and no-ops, got RC=$RC ERR=$ERRTEXT"; fi

  # A detector exiting outside its two verdicts is a failure even when what it
  # printed happens to parse: a crash must not read as a clean session.
  new_repo "$TMP/oddexit"
  HOOKPATH="$(stage_hook "$real")"
  printf '#!/bin/sh\necho %s\nexit 9\n' \
    "'{\"ok\":true,\"self\":null,\"others\":[],\"blocking\":[]}'" \
    > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
  run_hook "$TMP/oddexit" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'exited 9 instead of reporting a verdict'; then
    pass; else fail "an unexpected detector exit warns and no-ops, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # Unparseable detector output must not crash session start, and must not pass
  # for "nothing abandoned" either: a broken detector stays visible on stderr.
  new_repo "$TMP/garbage"
  HOOKPATH="$(stage_hook "$real")"
  printf '#!/bin/sh\necho "not json"\nexit 1\n' > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
  run_hook "$TMP/garbage" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'not the JSON envelope'; then
    pass; else fail "unparseable detector output warns and no-ops, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # An envelope whose fields are absent must not classify as "nothing
  # abandoned": that is the reassuring answer, and the one this hook exists to
  # rule out.
  local shape
  for shape in '{}' '{"self":{},"others":[{}],"ok":true,"blocking":[]}' \
               '{"ok":true,"self":null,"others":[{"path":"/x"}],"blocking":[]}' \
               '[]'; do
    new_repo "$TMP/shape"
    HOOKPATH="$(stage_hook "$real")"
    printf '#!/bin/sh\ncat <<JSON\n%s\nJSON\n' "$shape" \
      > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
    run_hook "$TMP/shape" "$HOOKPATH"
    if [[ $RC -eq 0 && -z "$OUT" ]] \
       && printf '%s' "$ERRTEXT" | grep -qF 'not the JSON envelope'; then
      pass; else fail "the envelope ${shape} warns and no-ops, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
    rm -rf "$TMP/shape"
  done

  # The detector's own stderr is the hook's too: a warning it emits about a path
  # it could not read must not vanish behind the hook's silence.
  new_repo "$TMP/relay"
  HOOKPATH="$(stage_hook "$real")"
  printf '#!/bin/sh\necho "check-leftovers: cannot read the modification time of /x" >&2\necho %s\n' \
    "'{\"ok\":true,\"self\":null,\"others\":[],\"blocking\":[]}'" \
    > "$(dirname "$HOOKPATH")/../skills/release/check-leftovers.sh"
  run_hook "$TMP/relay" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'detector: check-leftovers: cannot read the modification time'; then
    pass; else fail "a detector warning reaches the session's stderr, got RC=$RC ERR=$ERRTEXT"; fi

  # git absent is a real problem to name, not a quiet no-op. The interpreter is
  # named absolutely because the emptied PATH cannot find one, and `command -v
  # git` is the hook's first line, so nothing else is needed on it.
  new_repo "$TMP/nogit"
  HOOKPATH="$(stage_hook "$real")"
  local shell
  shell="$(command -v bash)" || die "bash not on PATH"
  mkdir -p "$TMP/emptybin"
  OUT="$(cd "$TMP/nogit" && PATH="$TMP/emptybin" "$shell" "$HOOKPATH" 2>"$ERRFILE")"; RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 0 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -qF 'git not found on PATH'; then
    pass; else fail "an absent git warns and no-ops, got RC=$RC ERR=$ERRTEXT"; fi

  # A repository git cannot read is not the same answer as standing outside one.
  new_repo "$TMP/badgitdir"
  HOOKPATH="$(stage_hook "$real")"
  OUT="$(cd "$TMP/badgitdir" && PATH="$CLOCKBIN:$PATH" GIT_DIR=/nonexistent/not-a-gitdir \
    bash "$HOOKPATH" 2>"$ERRFILE")"; RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -qF 'cannot read this repository'; then
    pass; else fail "an unreadable repository warns and no-ops, got RC=$RC ERR=$ERRTEXT"; fi

  # ...and standing outside one still says nothing, which is the case above it.
  HOOKPATH="$(stage_hook "$real")"
  run_hook "$TMP/notrepo" "$HOOKPATH"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$ERRTEXT" ]]; then
    pass; else fail "a non-repository warns about nothing, got RC=$RC ERR=$ERRTEXT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
