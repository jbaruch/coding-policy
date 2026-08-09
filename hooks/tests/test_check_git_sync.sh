#!/usr/bin/env bash
# Outcome-based tests for check-git-sync.sh.
#
# The hook shells out to real git, so tests build real local repos (a bare
# "origin" plus working clones) and drive git offline — no network, fully
# deterministic. The injected clock (SYNC_NOW) drives the throttle assertions.
#
# Each scenario builds its OWN bare origin + seed (mk_origin) so scenarios share
# no mutable state and run in any order (rules/testing-standards.md Independence).
# The harness drops `set -e` to aggregate results, so every fixture-setup command
# is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Behind        -> emits additionalContext naming the branch + "behind".
#   2. Up to date    -> silent (empty stdout), exit 0.
#   3. Throttle      -> with a fixed injected clock: a call inside the window
#                       skips the fetch (stays silent though origin moved); a
#                       call past the window fetches and fires.
#   4. Not a repo    -> silent no-op, exit 0.
#   5. No origin     -> silent no-op, exit 0.
#   6. Fetch failure -> silent no-op, exit 0 (offline/broken remote tolerated).
#   7. Bad clock     -> silent no-op, exit 0 (never aborts SessionStart).
#   8. Diverged      -> notice names divergence and recommends rebase, not a
#                       fast-forward (local both ahead and behind origin).
#
# Run: bash hooks/tests/test_check_git_sync.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

g() { git "$@"; }

commit_push() { # <clone-dir> <message>
  local dir="$1" msg="$2"
  printf '%s\n' "$msg" >> "$dir/f"                || die "commit_push: write to $dir/f failed"
  g -C "$dir" add f                               || die "commit_push: git add failed in $dir"
  g -C "$dir" commit -q -m "$msg"                 || die "commit_push: git commit failed in $dir"
  g -C "$dir" push -q origin main                 || die "commit_push: git push from $dir failed"
}

mk_origin() { # <prefix>: sets globals BARE, SEED to a fresh, independent origin
  local prefix="$1"
  BARE="$TMP/${prefix}.git"; SEED="$TMP/${prefix}-seed"
  g init -q --bare -b main "$BARE"                || die "mk_origin: git init --bare failed for $BARE"
  g clone -q "$BARE" "$SEED" 2>/dev/null          || die "mk_origin: git clone failed for $SEED"
  g -C "$SEED" symbolic-ref HEAD refs/heads/main  || die "mk_origin: git symbolic-ref failed in $SEED"
  commit_push "$SEED" "c1"
}

clone_from() { # <bare> <dest>: a working clone, checked explicitly
  g clone -q "$1" "$2"                            || die "clone_from: git clone $1 -> $2 failed"
}

# run <repo-dir> <state-dir> [extra env...] -> OUT, RC
run() {
  local repo="$1" state="$2"; shift 2
  OUT="$(cd "$repo" && env SYNC_STATE_DIR="$state" "$@" bash "$SCRIPT" </dev/null 2>/dev/null)"
  RC=$?
}

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-git-sync.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "hook not found/readable at $SCRIPT"
  command -v jq  >/dev/null 2>&1 || die "jq required for these tests"
  command -v git >/dev/null 2>&1 || die "git required for these tests"

  TMP="$(mktemp -d -t git-sync-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  # Isolate git from the operator's global/system config so identity and defaults
  # are deterministic across machines. This isolation is load-bearing — an
  # unchecked mkdir failure would silently defeat it, so guard it explicitly.
  export HOME="$TMP/home"
  mkdir -p "$HOME" || die "could not create isolated HOME at $HOME"
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

  FAIL=0; PASS=0

  # 1. behind -> notice. Clone (up to date), then move origin ahead by one
  #    commit; the hook fetches and reports behind by 1.
  mk_origin o1
  clone_from "$BARE" "$TMP/r1"
  commit_push "$SEED" "c2"
  run "$TMP/r1" "$TMP/s1"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind") and test("main")' >/dev/null 2>&1; then
    pass; else fail "behind: expected notice, got RC=$RC OUT=$OUT"; fi

  # 2. up to date -> silent.
  mk_origin o2
  clone_from "$BARE" "$TMP/r2"
  run "$TMP/r2" "$TMP/s2"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "up-to-date: expected silence, got RC=$RC OUT=$OUT"; fi

  # 3. throttle. Clone up to date. Call at t stamps and (nothing new) stays
  #    silent. Move origin ahead WITHOUT the hook fetching: a call +60s is
  #    throttled, so it skips the fetch and stays silent (proves no fetch). A
  #    call past the 1h window fetches the new commit and fires.
  mk_origin o3
  clone_from "$BARE" "$TMP/r3"
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2000000               # fetch, stamp, up to date -> silent
  [[ $RC -eq 0 && -z "$OUT" ]] || fail "throttle setup: first call should be silent, got RC=$RC OUT=$OUT"
  commit_push "$SEED" "c3"                                # origin moves; r3's tracking ref still old
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2000060               # +60s: throttled -> no fetch -> silent
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "throttle active: inside window should skip fetch and stay silent, got OUT=$OUT"; fi
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2003601               # +>1h: fetch -> behind -> fires
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind")' >/dev/null 2>&1; then
    pass; else fail "throttle expired: past window should fetch and fire, got OUT=$OUT"; fi

  # 4. not a repo -> silent no-op.
  mkdir -p "$TMP/notrepo" || die "could not create $TMP/notrepo"
  run "$TMP/notrepo" "$TMP/s4"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "not a repo: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 5. no origin remote -> silent no-op.
  g init -q -b main "$TMP/noorigin" || die "git init noorigin failed"
  printf 'x\n' > "$TMP/noorigin/f"  || die "write noorigin/f failed"
  g -C "$TMP/noorigin" add f        || die "git add in noorigin failed"
  g -C "$TMP/noorigin" commit -q -m x || die "git commit in noorigin failed"
  run "$TMP/noorigin" "$TMP/s5"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no origin: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 6. fetch failure -> silent no-op (broken remote tolerated, no crash). Clone,
  #    then delete the bare origin so the fetch fails; local == last-known
  #    origin, so the comparison yields 0 and the hook exits 0 without crashing.
  mk_origin o6
  clone_from "$BARE" "$TMP/r6"
  rm -rf "$BARE" || die "could not remove $BARE"
  run "$TMP/r6" "$TMP/s6"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "fetch failure: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 7. malformed clock -> no-op, exit 0.
  mk_origin o7
  clone_from "$BARE" "$TMP/r7"
  run "$TMP/r7" "$TMP/s7" SYNC_NOW="not-a-number"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "bad clock: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 8. diverged -> divergence notice (rebase, not fast-forward). Clone, add a
  #    local commit (ahead by 1), and push a different commit to origin (behind
  #    by 1); the hook fetches and reports the diverged state.
  mk_origin o8
  clone_from "$BARE" "$TMP/r8"
  printf 'local\n' >> "$TMP/r8/f"        || die "write r8/f failed"
  g -C "$TMP/r8" add f                    || die "git add in r8 failed"
  g -C "$TMP/r8" commit -q -m local       || die "git commit in r8 failed"   # ahead by 1
  commit_push "$SEED" "c2"                                                    # origin moves -> behind by 1
  run "$TMP/r8" "$TMP/s8"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("diverged") and test("rebase")' >/dev/null 2>&1; then
    pass; else fail "diverged: expected divergence notice, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
