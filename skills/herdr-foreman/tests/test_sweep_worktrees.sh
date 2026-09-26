#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/sweep-worktrees.sh.
#
# Real local git repos, offline: bare origins plus shared checkouts in a temp
# dir, with one temp worktree root holding every repository's worktrees.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Two repositories  -> each is pruned once; a merged worktree of each goes.
#   2. Skipped entries   -> a plain directory, a file and a clone under the
#                           root are reported, not fatal.
#   3. No origin         -> that repository reports an error, exit 2; the
#                           others still ran.
#   4. Dry run           -> the same decisions, nothing removed.
#   5. Usage             -> no root or a missing root is exit 1, no JSON.
#
# Run: bash skills/herdr-foreman/tests/test_sweep_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

G=(git -c user.name=t -c user.email=t@t)

mk_repo() { # <prefix> [no-origin] -> sets SHARED
  local prefix="$1" bare="$TMP/$1.git" seed="$TMP/$1-seed"
  SHARED="$TMP/$1-shared"
  if [[ "${2:-}" == no-origin ]]; then
    "${G[@]}" init -q -b main "$SHARED" || die "git init failed for $prefix"
    printf 'x\n' > "$SHARED/f" || die "seed write failed"
    "${G[@]}" -C "$SHARED" add f || die "git add failed"
    "${G[@]}" -C "$SHARED" commit -q -m c1 || die "git commit failed"
    return 0
  fi
  "${G[@]}" init -q --bare -b main "$bare" || die "git init --bare failed for $prefix"
  "${G[@]}" clone -q "$bare" "$seed" 2>/dev/null || die "git clone seed failed"
  printf 'x\n' > "$seed/f" || die "seed write failed"
  "${G[@]}" -C "$seed" add f || die "git add failed"
  "${G[@]}" -C "$seed" commit -q -m c1 || die "git commit failed"
  "${G[@]}" -C "$seed" push -q origin main || die "git push failed"
  "${G[@]}" clone -q "$bare" "$SHARED" 2>/dev/null || die "git clone shared failed"
  "${G[@]}" -C "$SHARED" remote set-head origin --auto >/dev/null || die "remote set-head failed"
}

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

q() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" <<<"$OUT"; }

listed() { # <shared> <path>
  local inventory
  inventory="$(git -C "$1" worktree list --porcelain)" || die "git worktree list failed in $1"
  grep -qx "worktree $2" <<<"$inventory"
}

main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sweep-worktrees.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT
  local root="$TMP/worktrees"
  mkdir -p "$root" || die "mkdir root failed"

  mk_repo alpha; local alpha="$SHARED"
  "${G[@]}" -C "$alpha" worktree add -q -b review/a "$root/alpha-merged" origin/main 2>/dev/null || die "alpha worktree failed"
  mk_repo beta; local beta="$SHARED"
  "${G[@]}" -C "$beta" worktree add -q -b review/b "$root/beta-merged" origin/main 2>/dev/null || die "beta worktree failed"
  mkdir -p "$root/plain" || die "mkdir plain failed"
  printf 'x\n' > "$root/afile" || die "write afile failed"
  "${G[@]}" clone -q "$TMP/alpha.git" "$root/a-clone" 2>/dev/null || die "clone under root failed"

  run "$root" --dry-run
  echo "4. a dry run reports every repository's decisions and removes nothing"
  if (( RC == 0 )) && [[ "$(q 'len(d["repos"])')" == 2 ]] \
    && [[ "$(q 'sum(len(r["result"]["worktrees_removed"]) for r in d["repos"])')" == 2 ]] \
    && listed "$alpha" "$root/alpha-merged" && listed "$beta" "$root/beta-merged"; then
    pass; else fail "dry run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  run "$root"
  echo "1. each repository is pruned once and its merged worktree removed"
  if (( RC == 0 )) && [[ "$(q '",".join(sorted(r["shared"] for r in d["repos"]))')" == "$alpha,$beta" ]] \
    && ! listed "$alpha" "$root/alpha-merged" && ! listed "$beta" "$root/beta-merged"; then
    pass; else fail "two repos: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "2. a plain directory, a file and a clone are skipped with their reasons"
  if [[ "$(q '",".join(s["reason"] for s in sorted(d["skipped"], key=lambda s: s["path"]))')" == "clone,not-a-worktree,not-a-worktree" ]]; then
    pass; else fail "skipped: out=$OUT"; fi

  mk_repo gamma no-origin; local gamma="$SHARED"
  "${G[@]}" -C "$gamma" worktree add -q -b review/g "$root/gamma-wt" 2>/dev/null || die "gamma worktree failed"
  "${G[@]}" -C "$alpha" worktree add -q -b review/a2 "$root/alpha-again" origin/main 2>/dev/null || die "alpha second worktree failed"
  run "$root"
  echo "3. a repository without origin reports an error; the others still ran"
  if (( RC == 2 )) && [[ "$(q 'next(r.get("error","") for r in d["repos"] if r["shared"].endswith("gamma-shared"))')" == *"no origin"* ]] \
    && ! listed "$alpha" "$root/alpha-again" && listed "$gamma" "$root/gamma-wt"; then
    pass; else fail "no origin: rc=$RC out=$OUT err=$ERRTEXT"; fi

  run
  echo "5a. no root is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *usage* ]]; then pass; else fail "rc=$RC out=$OUT"; fi
  run "$TMP/absent"
  echo "5b. a missing root is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT"; fi

  echo
  echo "passed=$PASS failed=$FAIL"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
