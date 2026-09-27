#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/prune-remote-branches.sh.
#
# A bare "origin" plus a clone of it, per case, and a fake `gh` first on PATH
# that answers from files in the case's own directory, so the cases share no
# state and run in any order (rules/testing-standards.md Independence). Commit
# dates and the script's clock are pinned (PRUNE_NOW), never the wall clock.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Merged, no pull request  -> deleted on origin, listed under deleted.
#   2. Open pull request        -> untouched, listed nowhere.
#   3. Unmerged, stale          -> questionable with ahead, age and author.
#   4. Unmerged, fresh          -> kept not-idle, still on origin.
#   5. gh failing               -> nothing deleted, could_not_check, exit 2.
#   6. gh absent                -> nothing deleted, could_not_check, exit 2.
#   7. Moved before deletion    -> the lease refuses, kept changed.
#   8. Protected branch         -> untouched, listed nowhere.
#   9. Dry run                  -> the deletion is previewed, origin unchanged.
#  10. Default branch           -> never judged.
#  11. Usage                    -> exit 1, no JSON.
#
# Run: bash skills/herdr-foreman/tests/test_prune_remote_branches.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../prune-remote-branches.sh"
PASS=0
FAIL=0
TMP=""

#: The pinned clock: 2026-01-10T00:00:00Z. Stale commits are dated 48h before
#: it, fresh ones 1h before it.
NOW=1768003200
STALE_DATE="@$((NOW - 48 * 3600)) +0000"
FRESH_DATE="@$((NOW - 3600)) +0000"

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() {
  if [[ -n "$TMP" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi
  return 0
}
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Run a fixture command, stderr captured: a failure stops the harness with the
# command's own words, never a silent exit.
quiet() { # <what> <command...>
  local what="$1" rc=0; shift
  "$@" 2>"$TMP/quiet.err" || rc=$?
  if (( rc != 0 )); then
    die "${what} (exit ${rc}): $(tr '\n' ' ' < "$TMP/quiet.err") — rerun \`$*\` by hand"
  fi
}

# A fake gh answering from <dir>: `prs` lists open pull-request heads, one per
# line; `protected` lists protected branches; `fail` makes every call exit 1;
# `on-head` is a script run when `pr list --head` is asked (a race injector).
write_fake_gh() { # <dir>
  mkdir -p "$1/bin" || die "cannot create $1/bin"
  cat > "$1/bin/gh" <<'SH' || die "cannot write the fake gh"
#!/usr/bin/env bash
set -euo pipefail
d="${FAKE_GH_DIR:?}"
if [[ -e "$d/fail" ]]; then echo "gh: HTTP 502 from https://token@example.invalid" >&2; exit 1; fi
case "$1" in
  api) [[ -f "$d/protected" ]] && cat "$d/protected"; exit 0 ;;
  pr)
    head=""
    while (( $# )); do
      if [[ "$1" == --head ]]; then head="$2"; shift; fi
      shift
    done
    if [[ -n "$head" && -f "$d/on-head" ]]; then bash "$d/on-head" "$head"; fi
    [[ -f "$d/prs" ]] || exit 0
    if [[ -n "$head" ]]; then grep -xF -- "$head" "$d/prs" || [[ $? -eq 1 ]]; else cat "$d/prs"; fi
    exit 0 ;;
esac
echo "fake gh: unexpected call $*" >&2
exit 3
SH
  chmod +x "$1/bin/gh" || die "cannot make the fake gh executable"
}

# Per case: a bare origin with main, a clone as the shared checkout, and the
# fake gh's directory. Sets CASE, BARE, SHARED.
mk_case() { # <name>
  CASE="$TMP/$1"
  BARE="$CASE/origin.git"
  SHARED="$CASE/shared"
  mkdir -p "$CASE" || die "cannot create $CASE"
  quiet "git init --bare" git init -q --bare -b main "$BARE"
  quiet "git clone" git clone -q "$BARE" "$SHARED"
  commit_on "$SHARED" main base "$STALE_DATE"
  quiet "push main" git -C "$SHARED" push -q origin main
  write_fake_gh "$CASE"
}

commit_on() { # <repo> <branch> <file> <date>
  quiet "switch $2" git -C "$1" switch -q -C "$2"
  printf '%s\n' "$3" > "$1/$3" || die "write $3 failed"
  quiet "add $3" git -C "$1" add "$3"
  quiet "commit $3" env GIT_AUTHOR_DATE="$4" GIT_COMMITTER_DATE="$4" \
    git -C "$1" -c user.name=Ada -c user.email=ada@example.invalid commit -q -m "$3"
}

# A branch on origin cut from main: merged (fast-forwarded into main) or not.
push_branch() { # <branch> <date> <merged 0|1>
  quiet "switch main" git -C "$SHARED" switch -q main
  quiet "branch $1" git -C "$SHARED" switch -q -c "$1"
  commit_on "$SHARED" "$1" "file-${1//\//-}" "$2"
  quiet "push $1" git -C "$SHARED" push -q origin "$1"
  quiet "switch main" git -C "$SHARED" switch -q main
  if (( $3 )); then
    quiet "merge $1" git -C "$SHARED" merge -q --ff-only "$1"
    quiet "push main" git -C "$SHARED" push -q origin main
  fi
}

on_origin() { git -C "$BARE" show-ref --verify --quiet "refs/heads/$1"; }

run() { # [--dry-run] ; sets OUT, RC, ERR
  RC=0
  OUT="$(PATH="$CASE/bin:$PATH" FAKE_GH_DIR="$CASE" PRUNE_NOW="$NOW" bash "$SCRIPT" "$SHARED" "$@" 2>"$CASE/err")" || RC=$?
  ERR="$(cat "$CASE/err")"
}

jq_py() { # <python expression over doc>
  printf '%s' "$OUT" | python3 -c 'import json,sys; doc=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"
}

main() {
  command -v python3 >/dev/null || die "python3 is required"
  TMP="$(mktemp -d)" || die "mktemp failed"
  trap cleanup EXIT

  echo "1. a merged branch with no pull request is deleted on origin"
  mk_case c1
  push_branch feat/done "$STALE_DATE" 1
  run
  if [[ $RC -eq 0 ]] && ! on_origin feat/done && [[ "$(jq_py 'doc["deleted"]')" == "['feat/done']" ]]; then pass
  else fail "c1: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "2. a branch with an open pull request is untouched and listed nowhere"
  mk_case c2
  push_branch feat/merged-pr "$STALE_DATE" 1
  push_branch feat/open-pr "$STALE_DATE" 0
  printf 'feat/merged-pr\nfeat/open-pr\n' > "$CASE/prs" || die "write prs failed"
  run
  if [[ $RC -eq 0 ]] && on_origin feat/merged-pr && on_origin feat/open-pr && [[ "$OUT" != *feat/* ]]; then pass
  else fail "c2: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "3. an unmerged stale branch is questionable, with ahead, age and author"
  mk_case c3
  push_branch feat/stale "$STALE_DATE" 0
  run
  if [[ $RC -eq 0 ]] && on_origin feat/stale \
     && [[ "$(jq_py '[(q["branch"], q["ahead"], q["age_hours"], q["author"]) for q in doc["questionable"]]')" == "[('feat/stale', 1, 48, 'Ada')]" ]] \
     && [[ "$(jq_py 'doc["questionable"][0]["open_pr"]')" == "gh pr create --head feat/stale" ]]; then pass
  else fail "c3: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "4. an unmerged fresh branch is kept not-idle"
  mk_case c4
  push_branch feat/fresh "$FRESH_DATE" 0
  run
  if [[ $RC -eq 0 ]] && on_origin feat/fresh && [[ "$(jq_py 'doc["kept"]')" == "[{'branch': 'feat/fresh', 'reason': 'not-idle'}]" ]] \
     && [[ "$(jq_py 'doc["questionable"]')" == "[]" ]]; then pass
  else fail "c4: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "5. a failing gh deletes nothing and says it could not check, never the URL"
  mk_case c5
  push_branch feat/done "$STALE_DATE" 1
  : > "$CASE/fail" || die "write fail flag failed"
  run
  if [[ $RC -eq 2 ]] && on_origin feat/done && [[ "$(jq_py 'doc["could_not_check"] is not None and not doc["deleted"]')" == True ]] \
     && [[ "$OUT$ERR" != *token@* ]]; then pass
  else fail "c5: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "6. an absent gh deletes nothing and says it could not check"
  mk_case c6
  push_branch feat/done "$STALE_DATE" 1
  rm -f "$CASE/bin/gh" || die "cannot remove the fake gh"
  local nogh="$CASE/nogh" tool
  mkdir -p "$nogh" || die "cannot create $nogh"
  for tool in git python3 bash env awk sed grep head tr cat mktemp rm; do
    ln -s "$(command -v "$tool")" "$nogh/$tool" || die "cannot link $tool"
  done
  RC=0
  OUT="$(PATH="$nogh" FAKE_GH_DIR="$CASE" PRUNE_NOW="$NOW" "$nogh/bash" "$SCRIPT" "$SHARED" 2>"$CASE/err")" || RC=$?
  ERR="$(cat "$CASE/err")"
  if [[ $RC -eq 2 ]] && on_origin feat/done && [[ "$(jq_py '"gh" in doc["could_not_check"] and not doc["deleted"]')" == True ]]; then pass
  else fail "c6: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "7. a branch pushed to after it was judged is kept by the lease"
  mk_case c7
  push_branch feat/raced "$STALE_DATE" 1
  quiet "clone racer" git clone -q "$BARE" "$CASE/racer"
  cat > "$CASE/on-head" <<SH || die "write on-head failed"
set -euo pipefail
git -C "$CASE/racer" switch -q feat/raced
printf 'late\n' > "$CASE/racer/late"
git -C "$CASE/racer" add late
git -C "$CASE/racer" -c user.name=Bo -c user.email=bo@example.invalid commit -q -m late
git -C "$CASE/racer" push -q origin feat/raced
rm -f "$CASE/on-head"
SH
  run
  if [[ $RC -eq 0 ]] && on_origin feat/raced && [[ "$(jq_py 'doc["kept"]')" == "[{'branch': 'feat/raced', 'reason': 'changed'}]" ]]; then pass
  else fail "c7: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "8. a protected branch is untouched and listed nowhere"
  mk_case c8
  push_branch release/1 "$STALE_DATE" 1
  push_branch release/2 "$STALE_DATE" 0
  printf 'release/1\nrelease/2\n' > "$CASE/protected" || die "write protected failed"
  run
  if [[ $RC -eq 0 ]] && on_origin release/1 && on_origin release/2 && [[ "$OUT" != *release/* ]]; then pass
  else fail "c8: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "9. a dry run previews the deletion and leaves origin unchanged"
  mk_case c9
  push_branch feat/done "$STALE_DATE" 1
  run --dry-run
  if [[ $RC -eq 0 ]] && on_origin feat/done && [[ "$(jq_py 'doc["deleted"] == ["feat/done"] and doc["dry_run"]')" == True ]]; then pass
  else fail "c9: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "10. the default branch is never judged"
  mk_case c10
  run
  if [[ $RC -eq 0 ]] && on_origin main && [[ "$(jq_py 'doc["deleted"] + doc["kept"] + doc["questionable"]')" == "[]" ]]; then pass
  else fail "c10: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "11. usage is exit 1 with no JSON"
  RC=0
  OUT="$(bash "$SCRIPT" 2>"$TMP/usage.err")" || RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q usage "$TMP/usage.err"; then pass
  else fail "usage: RC=$RC OUT=$OUT"; fi

  echo
  echo "passed=${PASS} failed=${FAIL}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
