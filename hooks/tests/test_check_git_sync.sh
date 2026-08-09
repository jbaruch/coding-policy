#!/usr/bin/env bash
# Outcome-based tests for check-git-sync.sh.
#
# The hook shells out to real git, so tests build real local repos (a bare
# "origin" plus working clones) and drive git offline — no network, fully
# deterministic. The injected clock (SYNC_NOW) drives the throttle assertions.
#
# Each scenario builds its OWN bare origin + seed (mk_origin) so scenarios share
# no mutable state and run in any order (rules/testing-standards.md Independence).
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

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/check-git-sync.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: hook not found/readable at $SCRIPT" >&2; exit 2; }
command -v jq  >/dev/null 2>&1 || { echo "fatal: jq required for these tests"  >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "fatal: git required for these tests" >&2; exit 2; }

TMP="$(mktemp -d -t git-sync-test.XXXXXX)" || { echo "fatal: mktemp failed" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
trap cleanup EXIT

# Isolate git from the operator's global/system config so identity and defaults
# are deterministic across machines. This isolation is load-bearing — an
# unchecked mkdir failure would silently defeat it, so guard it explicitly
# (rules/error-handling.md aggregate-reporting carve-out, setup-step check).
export HOME="$TMP/home"
mkdir -p "$HOME" || { echo "fatal: could not create isolated HOME at $HOME" >&2; exit 2; }
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

g() { git "$@"; }
commit_push() { # <clone-dir> <message>
  local dir="$1" msg="$2"
  printf '%s\n' "$msg" >> "$dir/f"
  g -C "$dir" add f
  g -C "$dir" commit -q -m "$msg"
  g -C "$dir" push -q origin main
}
mk_origin() { # <prefix>: sets globals BARE, SEED to a fresh, independent origin
  local prefix="$1"
  BARE="$TMP/${prefix}.git"; SEED="$TMP/${prefix}-seed"
  g init -q --bare -b main "$BARE"
  g clone -q "$BARE" "$SEED" 2>/dev/null   # cloning an empty bare warns; irrelevant here
  g -C "$SEED" symbolic-ref HEAD refs/heads/main
  commit_push "$SEED" "c1"
}

# run <repo-dir> <state-dir> [extra env...] -> OUT, RC
run() {
  local repo="$1" state="$2"; shift 2
  OUT="$(cd "$repo" && env SYNC_STATE_DIR="$state" "$@" bash "$SCRIPT" </dev/null 2>/dev/null)"
  RC=$?
}

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# 1. behind -> notice. Clone (up to date), then move origin ahead by one commit;
#    the hook fetches and reports behind by 1.
mk_origin o1
R1="$TMP/r1"; g clone -q "$BARE" "$R1"
commit_push "$SEED" "c2"
run "$R1" "$TMP/s1"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind") and test("main")' >/dev/null 2>&1; then
  pass; else fail "behind: expected notice, got RC=$RC OUT=$OUT"; fi

# 2. up to date -> silent.
mk_origin o2
R2="$TMP/r2"; g clone -q "$BARE" "$R2"
run "$R2" "$TMP/s2"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "up-to-date: expected silence, got RC=$RC OUT=$OUT"; fi

# 3. throttle. Clone up to date. Call at t stamps and (nothing new) stays silent.
#    Move origin ahead WITHOUT the hook fetching: a call +60s is throttled, so it
#    skips the fetch and stays silent (proves no fetch). A call past the 1h
#    window fetches the new commit and fires.
mk_origin o3
R3="$TMP/r3"; g clone -q "$BARE" "$R3"; s3="$TMP/s3"
run "$R3" "$s3" SYNC_NOW=2000000                       # fetch, stamp, up to date -> silent
[[ $RC -eq 0 && -z "$OUT" ]] || fail "throttle setup: first call should be silent, got RC=$RC OUT=$OUT"
commit_push "$SEED" "c3"                               # origin moves; R3's tracking ref still old
run "$R3" "$s3" SYNC_NOW=2000060                       # +60s: throttled -> no fetch -> silent
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "throttle active: inside window should skip fetch and stay silent, got OUT=$OUT"; fi
run "$R3" "$s3" SYNC_NOW=2003601                       # +>1h: fetch -> behind -> fires
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind")' >/dev/null 2>&1; then
  pass; else fail "throttle expired: past window should fetch and fire, got OUT=$OUT"; fi

# 4. not a repo -> silent no-op.
ND="$TMP/notrepo"
mkdir -p "$ND" || { echo "fatal: could not create $ND" >&2; exit 2; }
run "$ND" "$TMP/s4"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "not a repo: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 5. no origin remote -> silent no-op.
NO="$TMP/noorigin"; g init -q -b main "$NO"
printf 'x\n' > "$NO/f"; g -C "$NO" add f; g -C "$NO" commit -q -m x
run "$NO" "$TMP/s5"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no origin: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 6. fetch failure -> silent no-op (broken remote tolerated, no crash). Clone,
#    then delete the bare origin so the fetch fails; local == last-known origin,
#    so the comparison yields 0 and the hook exits 0 without crashing.
mk_origin o6
R6="$TMP/r6"; g clone -q "$BARE" "$R6"
rm -rf "$BARE" || { echo "fatal: could not remove $BARE" >&2; exit 2; }
run "$R6" "$TMP/s6"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "fetch failure: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 7. malformed clock -> no-op, exit 0.
mk_origin o7
R7="$TMP/r7"; g clone -q "$BARE" "$R7"
run "$R7" "$TMP/s7" SYNC_NOW="not-a-number"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "bad clock: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 8. diverged -> divergence notice (rebase, not fast-forward). Clone, add a local
#    commit (ahead by 1), and push a different commit to origin (behind by 1);
#    the hook fetches and reports the diverged state.
mk_origin o8
R8="$TMP/r8"; g clone -q "$BARE" "$R8"
printf 'local\n' >> "$R8/f"; g -C "$R8" add f; g -C "$R8" commit -q -m local   # ahead by 1
commit_push "$SEED" "c2"                                                        # origin moves -> behind by 1
run "$R8" "$TMP/s8"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("diverged") and test("rebase")' >/dev/null 2>&1; then
  pass; else fail "diverged: expected divergence notice, got RC=$RC OUT=$OUT"; fi

echo "─────────────────────────────────────────────" >&2
if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
echo "PASSED: all ${PASS} checks" >&2
