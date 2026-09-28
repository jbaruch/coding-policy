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
#   6. Nested            -> a worktree below an intermediate directory is found.
#   7. Stale metadata    -> a .git file naming a vanished gitdir is broken-worktree.
#   8. Unreadable        -> a worktree git cannot read is an error entry, exit 2.
#   9. Deep, linked      -> a worktree six levels down is found; a symlinked
#                           directory is not descended.
#  10. Attributed error  -> an error names its repository when the gitdir's
#                           files resolve it.
#  12. Unreadable result -> a prune exiting 0 without readable JSON fails the
#                           sweep (exit 2, stderr diagnostic).
#  13. No git            -> exit 1 with an actionable message, no traceback.
#  14. Symlinked dirs    -> a symlinked top-level directory, or a symlinked
#                           entry below one, never names a repository.
#  15. Report            -> the ready-to-relay report: a headline with counts,
#                           notable kept worktrees by path, the rest as counts.
#  16. Unreadable root   -> exit 1, no JSON, a repair message.
#  17. Unreadable subdir -> an errors entry, exit 2; the rest still swept.
#  18. Non-UTF-8 path    -> valid JSON naming it; kept idle-unknown, never a
#                           decode traceback (skipped where the filesystem
#                           refuses such a name, as macOS APFS does).
#  19. Symlinked .git    -> a directory whose .git is a symlink names no
#                           repository; the walk reports it.
#  22. Root replaced        -> a root swapped after discovery: exit 1, no
#                             prune.
#  23. Root replaced later  -> a root swapped after one prune ran: exit 2,
#                             an errors entry, no later prune.
#  24. Replaced, no repos  -> a root swapped after a discovery that found no
#                             repository: still exit 1, no JSON.
#  21. Copied .git         -> a .git file whose repository does not register
#                             the path is an errors entry; no prune runs.
#  20. Unreadable path   -> a directory lstat cannot read, or a worktree whose
#                           gitdir cannot be stat'ed, is an errors entry (exit
#                           2), never a skip or a stale worktree.
#
# Run: bash skills/herdr-foreman/tests/test_sweep_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

G=(git -c user.name=t -c user.email=t@t)

# Run a fixture command, stderr captured: on failure the harness stops with
# the command's own words and the command to rerun, never a silent exit.
quiet() { # <what> <command...>
  local what="$1" rc=0; shift
  "$@" 2>"$TMP/quiet.err" || rc=$?
  if (( rc != 0 )); then
    die "${what} (exit ${rc}): $(tr '\n' ' ' < "$TMP/quiet.err") — rerun \`$*\` by hand to see the whole failure"
  fi
}

# Give the calling case its own two repositories, rebinding main's alpha and
# beta, so no case reads another's fixtures.
fresh_pair() { # <case-tag>
  mk_repo "alpha$1"; alpha="$SHARED"
  mk_repo "beta$1"; beta="$SHARED"
}

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
  quiet "git clone seed failed" "${G[@]}" clone -q "$bare" "$seed"
  printf 'x\n' > "$seed/f" || die "seed write failed"
  "${G[@]}" -C "$seed" add f || die "git add failed"
  "${G[@]}" -C "$seed" commit -q -m c1 || die "git commit failed"
  "${G[@]}" -C "$seed" push -q origin main || die "git push failed"
  quiet "git clone shared failed" "${G[@]}" clone -q "$bare" "$SHARED"
  "${G[@]}" -C "$SHARED" remote set-head origin --auto >/dev/null || die "remote set-head failed"
}

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PRUNE_IDLE_HOURS=0 bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

q() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" <<<"$OUT"; }

listed() { # <shared> <path>  -> 0 listed, 1 not listed; any other failure aborts the harness
  local inventory rc=0
  inventory="$(git -C "$1" worktree list --porcelain)" || die "git worktree list failed in $1"
  grep -qxF "worktree $2" <<<"$inventory" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "grep failed (exit $rc) reading the worktree inventory" ;; esac
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

main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sweep-worktrees.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT
  # The operator's git config never reaches the script under test: no global
  # identity, as on a CI runner, so every commit the script writes must carry
  # its own. Fixtures pass theirs with -c.
  : > "$TMP/gitconfig" || die "cannot create an empty global git config"
  export GIT_CONFIG_GLOBAL="$TMP/gitconfig" GIT_CONFIG_NOSYSTEM=1
  write_fake_lsof "$TMP/fake-lsof/lsof"
  export PRUNE_LSOF="$TMP/fake-lsof/lsof" FAKE_LSOF_CWDS="$TMP/fake-lsof/cwds"
  # --- 4. dry run, on its own two repositories and root.
  local droot="$TMP/worktrees-dry"
  mkdir -p "$droot" || die "mkdir dry root failed"
  mk_repo dalpha; local dalpha="$SHARED"
  quiet "dalpha worktree failed" "${G[@]}" -C "$dalpha" worktree add -q -b review/a "$droot/dalpha-merged" origin/main
  mk_repo dbeta; local dbeta="$SHARED"
  quiet "dbeta worktree failed" "${G[@]}" -C "$dbeta" worktree add -q -b review/b "$droot/dbeta-merged" origin/main
  run "$droot" --dry-run
  echo "4. a dry run reports every repository's decisions and removes nothing"
  if (( RC == 0 )) && [[ "$(q 'len(d["repos"])')" == 2 ]] \
    && [[ "$(q 'sum(len(r["result"]["worktrees_removed"]) for r in d["repos"])')" == 2 ]] \
    && listed "$dalpha" "$droot/dalpha-merged" && listed "$dbeta" "$droot/dbeta-merged"; then
    pass; else fail "dry run: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 1-2. live run, on repositories and a root no other check touched.
  local root="$TMP/worktrees"
  mkdir -p "$root" || die "mkdir root failed"
  mk_repo alpha; local alpha="$SHARED"
  quiet "alpha worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/a "$root/alpha-merged" origin/main
  mk_repo beta; local beta="$SHARED"
  quiet "beta worktree failed" "${G[@]}" -C "$beta" worktree add -q -b review/b "$root/beta-merged" origin/main
  mkdir -p "$root/plain" || die "mkdir plain failed"
  printf 'x\n' > "$root/afile" || die "write afile failed"
  quiet "clone under root failed" "${G[@]}" clone -q "$TMP/alpha.git" "$root/a-clone"
  run "$root"
  echo "1. each repository is pruned once and its merged worktree removed"
  if (( RC == 0 )) && [[ "$(q '",".join(sorted(r["shared"] for r in d["repos"]))')" == "$alpha,$beta" ]] \
    && ! listed "$alpha" "$root/alpha-merged" && ! listed "$beta" "$root/beta-merged"; then
    pass; else fail "two repos: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "2. a plain directory, a file and a clone are skipped with their reasons"
  if [[ "$(q '",".join(s["reason"] for s in sorted(d["skipped"], key=lambda s: s["path"]))')" == "clone,not-a-worktree,not-a-worktree" ]]; then
    pass; else fail "skipped: out=$OUT"; fi

  # --- 3. no origin, on its own root.
  fresh_pair 3
  local nroot="$TMP/worktrees-noorigin"
  mkdir -p "$nroot" || die "mkdir no-origin root failed"
  mk_repo gamma no-origin; local gamma="$SHARED"
  quiet "gamma worktree failed" "${G[@]}" -C "$gamma" worktree add -q -b review/g "$nroot/gamma-wt"
  quiet "alpha second worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/a2 "$nroot/alpha-again" origin/main
  run "$nroot"
  echo "3. a repository without origin reports an error; the others still ran"
  if (( RC == 2 )) && [[ "$(q 'next(r.get("error","") for r in d["repos"] if r["shared"].endswith("gamma-shared"))')" == *"no origin"* ]] \
    && ! listed "$alpha" "$nroot/alpha-again" && listed "$gamma" "$nroot/gamma-wt"; then
    pass; else fail "no origin: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 6-8 on a fresh root.
  fresh_pair 6
  local root2="$TMP/worktrees2"
  mkdir -p "$root2/group/sub" || die "mkdir nested failed"
  quiet "nested worktree failed" "${G[@]}" -C "$beta" worktree add -q -b review/nested "$root2/group/sub/beta-nested" origin/main
  mkdir -p "$root2/stale" || die "mkdir stale failed"
  printf 'gitdir: %s\n' "$TMP/vanished/gitdir" > "$root2/stale/.git" || die "write stale .git failed"
  quiet "unreadable worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/unreadable "$root2/unreadable" origin/main
  local ugitdir
  ugitdir="$(git -C "$root2/unreadable" rev-parse --absolute-git-dir)" || die "rev-parse unreadable gitdir failed"
  printf '%s\n' "$TMP/no-such-common" > "$ugitdir/commondir" || die "corrupt commondir failed"
  run "$root2"
  echo "6. a worktree below an intermediate directory is found and pruned"
  if ! listed "$beta" "$root2/group/sub/beta-nested" && [[ "$(q '[s["path"] for s in d["skipped"] if s["path"].endswith("/group")]')" == "[]" ]]; then
    pass; else fail "nested: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "7. a .git file naming a vanished gitdir is broken-worktree"
  if [[ "$(q 'next((s["reason"] for s in d["skipped"] if s["path"].endswith("/stale")), "")')" == broken-worktree ]]; then
    pass; else fail "stale: out=$OUT"; fi
  echo "8. a worktree git cannot read is an error entry, exit 2"
  if (( RC == 2 )) && [[ "$(q 'next((e["path"] for e in d["errors"]), "")')" == "$root2/unreadable" ]] \
    && [[ "$(q 'next((str(e["repo"]) for e in d["errors"]), "")')" == None ]] && [[ "$ERRTEXT" == *"cannot read the worktree"* ]]; then
    pass; else fail "unreadable: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 9-10 on a third root.
  fresh_pair 9
  local root3="$TMP/worktrees3" deep="$TMP/worktrees3/a/b/c/d/e/f/beta-deep"
  mkdir -p "$(dirname "$deep")" "$TMP/elsewhere" || die "mkdir root3 failed"
  quiet "deep worktree failed" "${G[@]}" -C "$beta" worktree add -q -b review/deep "$deep" origin/main
  quiet "linked worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/linked "$TMP/elsewhere/alpha-linked" origin/main
  ln -s "$TMP/elsewhere" "$root3/link" || die "symlink failed"
  quiet "badhead worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/badhead "$root3/badhead" origin/main
  local bgitdir
  bgitdir="$(git -C "$root3/badhead" rev-parse --absolute-git-dir)" || die "rev-parse badhead gitdir failed"
  printf 'garbage\n' > "$bgitdir/HEAD" || die "corrupt HEAD failed"
  run "$root3"
  echo "9. a deep worktree is found; a symlinked directory is not descended"
  if ! listed "$beta" "$deep" && listed "$alpha" "$TMP/elsewhere/alpha-linked" \
    && [[ "$(q '[s["reason"] for s in d["skipped"] if s["path"].endswith("/link")]')" == "['not-a-worktree']" ]]; then
    pass; else fail "deep/linked: rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "10. an unreadable worktree names its repository when its gitdir resolves it"
  if (( RC == 2 )) && [[ "$(q 'next((e["repo"] for e in d["errors"] if e["path"].endswith("/badhead")), "")')" == "$alpha" ]]; then
    pass; else fail "attributed error: rc=$RC out=$OUT"; fi

  # --- 12. a prune exiting 0 with unreadable JSON fails the sweep.
  fresh_pair 12
  local shadow="$TMP/shadow12"
  mkdir -p "$shadow" || die "mkdir shadow failed"
  cp "$SCRIPT" "$shadow/" || die "copy sweep failed"
  cp -R "$(dirname "$SCRIPT")/foreman" "$shadow/" || die "copy the foreman package failed"
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "not json\\n"\n' > "$shadow/prune-worktrees.sh" || die "stub prune failed"
  local root12="$TMP/worktrees12"
  mkdir -p "$root12" || die "mkdir root12 failed"
  quiet "alpha twelve failed" "${G[@]}" -C "$alpha" worktree add -q -b review/twelve "$root12/alpha-twelve" origin/main
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(bash "$shadow/sweep-worktrees.sh" "$root12" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "12. a prune exiting 0 without readable JSON fails the sweep"
  if (( RC == 2 )) && [[ "$(q '"error" in d["repos"][0] and "result" not in d["repos"][0]')" == True ]] \
    && [[ "$ERRTEXT" == *"without a readable JSON result"* ]]; then
    pass; else fail "unreadable result: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 12b. a prune exiting 0 with JSON outside its shape fails the sweep,
  # and the sweep still prints its own JSON and report.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%%s\\n" '"'"'{"worktrees_removed":[],"worktrees_kept":[null],"branches_deleted":[],"branches_kept":[],"failed":[]}'"'"'\n' \
    > "$shadow/prune-worktrees.sh" || die "stub malformed prune failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(bash "$shadow/sweep-worktrees.sh" "$root12" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "12b. a prune result outside its documented shape is a repository error, never a crash"
  if (( RC == 2 )) && [[ "$(q '"malformed worktrees_kept" in d["repos"][0]["error"] and "result" not in d["repos"][0]')" == True ]] \
    && [[ "$(q '"Error in" in d["report"]')" == True ]]; then
    pass; else fail "malformed result: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 13. git missing from PATH.
  local nogit="$TMP/nogit" tool
  mkdir -p "$nogit" || die "mkdir nogit failed"
  for tool in bash python3 dirname; do
    ln -s "$(command -v "$tool")" "$nogit/$tool" || die "link $tool failed"
  done
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$nogit" "$BASH" "$SCRIPT" "$root" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "13. no git on PATH is exit 1 with an actionable message"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"git not found on PATH"* ]] && [[ "$ERRTEXT" != *Traceback* ]]; then
    pass; else fail "no git: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 14. a symlinked top-level directory, and a symlinked entry below one.
  fresh_pair 14
  local root5="$TMP/worktrees5" root6="$TMP/worktrees6" outside="$TMP/outside-linked"
  mkdir -p "$root5" "$root6/linked" "$outside" || die "mkdir root5/6 failed"
  quiet "outside worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/outside "$outside/alpha-out" origin/main
  ln -s "$outside" "$root5/linked" || die "symlink linked failed"
  ln -s "$outside/alpha-out" "$root6/linked/alpha-out" || die "symlink linked entry failed"
  local repos5 repos6
  run "$root5"; repos5="$(q 'len(d["repos"])')"
  run "$root6"; repos6="$(q 'len(d["repos"])')"
  echo "14. a symlinked directory, at the top or below, names no repository"
  if [[ "$repos5" == 0 && "$repos6" == 0 ]] && listed "$alpha" "$outside/alpha-out"; then
    pass; else fail "symlinked directory: repos5=$repos5 repos6=$repos6 out=$OUT"; fi

  # --- 15. the report, on its own repository and root.
  local root15="$TMP/worktrees15"
  mkdir -p "$root15" || die "mkdir root15 failed"
  mk_repo epsilon; local eps="$SHARED"
  quiet "eps merged failed" "${G[@]}" -C "$eps" worktree add -q -b review/e1 "$root15/eps-merged" origin/main
  quiet "eps held failed" "${G[@]}" -C "$eps" worktree add -q -b review/e2 "$root15/eps-held" origin/main
  "${G[@]}" -C "$eps" worktree lock --reason "operator hold" "$root15/eps-held" || die "lock failed"
  quiet "eps local failed" "${G[@]}" -C "$eps" worktree add -q -b feat/e3 "$root15/eps-local" origin/main
  printf 'e\n' > "$root15/eps-local/e.txt" || die "eps write failed"
  "${G[@]}" -C "$root15/eps-local" add e.txt || die "eps add failed"
  "${G[@]}" -C "$root15/eps-local" commit -q -m e || die "eps commit failed"
  run "$root15"
  local want age15
  age15="$(q 'next(k["age_hours"] for k in d["repos"][0]["result"]["worktrees_kept"] if k["reason"] == "unpushed")')"
  want="$(printf 'Worktree sweep: 1 repositories, 1 worktree(s) removed, 0 branch(es) deleted.\nKept locked: %s (operator hold)\nKept unpushed: %s (feat/e3), 1 commit(s) origin does not hold, idle %sh: git -C %s push -u origin HEAD' "$root15/eps-held" "$root15/eps-local" "$age15" "$root15/eps-local")"
  echo "15. the report names notable kept worktrees by path and counts the rest"
  if (( RC == 0 )) && [[ "$(q 'd["report"]')" == "$want" ]]; then
    pass; else fail "report: rc=$RC report=$(q 'd["report"]') want=$want"; fi

  # --- 16-17. unreadable root, unreadable directory under a root (skipped as root).
  fresh_pair 16
  if [[ "$(id -u)" == 0 ]]; then
    echo "16-17. skipped: root reads every directory"
  else
    local root16="$TMP/worktrees16" root17="$TMP/worktrees17"
    mkdir -p "$root16" "$root17/locked-away/deeper" || die "mkdir root16/17 failed"
    chmod 000 "$root16" || die "chmod root16 failed"
    run "$root16"
    chmod 755 "$root16" || die "restore root16 failed"
    echo "16. an unreadable root is exit 1 with no JSON and a repair message"
    if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"missing or unreadable"* ]] && [[ "$ERRTEXT" != *Traceback* ]]; then
      pass; else fail "unreadable root: rc=$RC out=$OUT err=$ERRTEXT"; fi
    quiet "alpha-17 failed" "${G[@]}" -C "$alpha" worktree add -q -b review/seventeen "$root17/alpha-17" origin/main
    chmod 000 "$root17/locked-away" || die "chmod locked-away failed"
    run "$root17"
    chmod 755 "$root17/locked-away" || die "restore locked-away failed"
    echo "17. an unreadable directory under the root is an errors entry; the rest is still swept"
    if (( RC == 2 )) && [[ "$(q 'next((e["path"] for e in d["errors"]), "")')" == "$root17/locked-away" ]] \
      && ! listed "$alpha" "$root17/alpha-17" && [[ "$ERRTEXT" != *Traceback* ]]; then
      pass; else fail "unreadable subdir: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 18. a worktree whose path is not valid UTF-8.
  fresh_pair 18
  local root18="$TMP/worktrees18" bad
  bad="$root18/bad$(printf '\377')wt"
  mkdir -p "$root18" || die "mkdir root18 failed"
  if ! mkdir "$bad" 2>"$TMP/mk18.err"; then
    echo "18. skipped: this filesystem refuses a non-UTF-8 name ($(cat "$TMP/mk18.err"))"
  else
    rmdir "$bad" || die "rmdir probe failed"
    quiet "non-UTF-8 worktree add failed" "${G[@]}" -C "$alpha" worktree add -q -b review/badname "$bad" origin/main
    run "$root18"
    echo "18. a non-UTF-8 worktree path yields valid JSON, the worktree kept as idle-unknown"
    if (( RC == 0 )) && [[ "$ERRTEXT" != *Traceback* ]] \
      && [[ "$(q '[k["reason"] for r in d["repos"] for k in r["result"]["worktrees_kept"] if "/worktrees18/" in k["path"]]')" == "['idle-unknown']" ]] \
      && listed "$alpha" "$bad"; then
      pass; else fail "non-UTF-8 path: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 19. a symlinked .git, one level down and at the top of a walked directory.
  fresh_pair 19
  local root19="$TMP/worktrees19" ext="$TMP/outside19"
  mkdir -p "$root19/nested/entry" "$root19/linked" "$ext" || die "mkdir root19 failed"
  quiet "outside worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/nineteen "$ext/alpha-19" origin/main
  ln -s "$ext/alpha-19/.git" "$root19/nested/entry/.git" || die "symlink nested .git failed"
  ln -s "$ext/alpha-19/.git" "$root19/linked/.git" || die "symlink walked .git failed"
  run "$root19"
  echo "19. a symlinked .git names no repository; the walked one is reported as symlinked-git"
  if (( RC == 0 )) && [[ "$(q 'len(d["repos"])')" == 0 ]] \
    && [[ "$(q '[s["reason"] for s in d["skipped"] if s["path"].endswith("/linked")]')" == "['symlinked-git']" ]] \
    && listed "$alpha" "$ext/alpha-19"; then
    pass; else fail "symlinked .git: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 20. unreadable paths are errors, never skips or stale worktrees.
  if [[ "$(id -u)" == 0 ]]; then
    echo "20. skipped: root reads every path"
  else
    fresh_pair 20
    local root20="$TMP/worktrees20" gd20
    mkdir -p "$root20/noexec" || die "mkdir root20 failed"
    printf 'gitdir: /nowhere\n' > "$root20/noexec/.git" || die "write .git failed"
    chmod 644 "$root20/noexec" || die "chmod noexec failed"
    quiet "alpha-20 failed" "${G[@]}" -C "$alpha" worktree add -q -b review/twenty "$root20/alpha-20" origin/main
    gd20="$(git -C "$root20/alpha-20" rev-parse --absolute-git-dir)" || die "rev-parse gitdir failed"
    chmod 000 "$(dirname "$gd20")" || die "chmod worktrees dir failed"
    run "$root20"
    chmod 755 "$(dirname "$gd20")" || die "restore worktrees dir failed"
    chmod 755 "$root20/noexec" || die "restore noexec failed"
    echo "20. an unreadable path and an unreadable gitdir are errors entries, exit 2"
    if (( RC == 2 )) && [[ "$(q 'sorted(e["path"].rsplit("/",1)[1] for e in d["errors"])')" == "['alpha-20', 'noexec']" ]] \
      && [[ "$(q '[s["path"] for s in d["skipped"] if s["reason"] in ("broken-worktree","not-a-worktree")]')" == "[]" ]]; then
      pass; else fail "unreadable paths: rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 22. a root replaced after discovery prunes nothing.
  fresh_pair 22
  local root22="$TMP/worktrees22" shim22="$TMP/shim22" real_git
  real_git="$(command -v git)" || die "git not found"
  mkdir -p "$root22" "$shim22" || die "mkdir root22 failed"
  quiet "alpha22 worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/r22 "$root22/alpha-22" origin/main
  # The sweep's first worktree-list read (after discovery, before any prune)
  # swaps the root for an empty directory of the same name, then runs the
  # real git. Without the recheck the prune would then run, find the moved
  # worktree gone, and delete its merged branch.
  # shellcheck disable=SC2016  # The $@ and $0 belong to the shim.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *"worktree list"* && ! -e %q ]]; then : > %q; mv %q %q; mkdir %q; fi\nexec %q "$@"\n' \
    "$shim22/done" "$shim22/done" "$root22" "$root22.moved" "$root22" "$real_git" > "$shim22/git" || die "write the git shim failed"
  chmod +x "$shim22/git" || die "chmod the git shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$shim22:$PATH" PRUNE_IDLE_HOURS=0 bash "$SCRIPT" "$root22" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "22. a root replaced after discovery is exit 1, no JSON, and nothing is pruned"
  if (( RC == 1 )) && [[ -z "$OUT" && "$ERRTEXT" == *"was replaced"*"nothing was pruned"* ]] \
    && git -C "$alpha" show-ref --verify --quiet refs/heads/review/r22; then
    pass; else fail "replaced root: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 23. a root replaced after one prune ran stops the rest.
  fresh_pair 23
  local root23="$TMP/worktrees23" shim23="$TMP/shim23"
  mkdir -p "$root23" "$shim23" || die "mkdir root23 failed"
  quiet "alpha23 worktree failed" "${G[@]}" -C "$alpha" worktree add -q -b review/a23 "$root23/alpha-23" origin/main
  quiet "beta23 worktree failed" "${G[@]}" -C "$beta" worktree add -q -b review/b23 "$root23/beta-23" origin/main
  # The first repository's metadata prune (a live run's, after its worktree
  # decisions) swaps the root for an empty directory of the same name. The
  # second repository's prune would then find its moved worktree gone and
  # delete its merged branch.
  # shellcheck disable=SC2016  # The $@ and $0 belong to the shim.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *"worktree prune"* && ! -e %q ]]; then : > %q; mv %q %q; mkdir %q; fi\nexec %q "$@"\n' \
    "$shim23/done" "$shim23/done" "$root23" "$root23.moved" "$root23" "$real_git" > "$shim23/git" || die "write the git shim failed"
  chmod +x "$shim23/git" || die "chmod the git shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$shim23:$PATH" PRUNE_IDLE_HOURS=0 bash "$SCRIPT" "$root23" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "23. a root replaced after one prune ran is exit 2, and no later repository is pruned"
  if (( RC == 2 )) && [[ -e "$shim23/done" ]] \
    && [[ "$(q 'len(d["repos"])')" == 1 ]] \
    && [[ "$(q '[e["error"] for e in d["errors"] if e["path"] == d["root"]][0]')" == *"was replaced during the sweep; 1 repository was not pruned"* ]] \
    && git -C "$beta" show-ref --verify --quiet refs/heads/review/b23 && listed "$beta" "$root23/beta-23"; then
    pass; else fail "root replaced mid-sweep: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 21. a copied .git file naming an unrelated repository runs no prune.
  mk_repo gamma21; local gamma="$SHARED" root21="$TMP/worktrees21"
  # The worktree's parent is made here, never left to git to create.
  mkdir -p "$root21/copied" "$TMP/outside21" || die "mkdir root21 failed"
  quiet "gamma worktree failed" "${G[@]}" -C "$gamma" worktree add -q -b review/gamma "$TMP/outside21/gamma-wt" origin/main
  quiet "gamma branch failed" "${G[@]}" -C "$gamma" branch -q --no-track review/gamma-merged origin/main
  cp "$TMP/outside21/gamma-wt/.git" "$root21/copied/.git" || die "copy the .git file failed"
  run "$root21"
  echo "21. a .git file its repository does not register is an errors entry, and that repository is never pruned"
  if (( RC == 2 )) && [[ "$(q 'len(d["repos"])')" == 0 ]] \
    && [[ "$(q '[(e["path"].rsplit("/",1)[1], e["repo"]) for e in d["errors"]]')" == "[('copied', None)]" ]] \
    && git -C "$gamma" show-ref --verify --quiet refs/heads/review/gamma-merged; then
    pass; else fail "copied .git: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 24. a root replaced after a discovery that found no repository is
  #     still exit 1: the check does not ride on the prune loop.
  mk_repo delta24; local delta="$SHARED" root24="$TMP/worktrees24" shim24="$TMP/shim24"
  mkdir -p "$root24/copied" "$TMP/outside24" "$shim24" || die "mkdir root24 failed"
  quiet "delta worktree failed" "${G[@]}" -C "$delta" worktree add -q -b review/delta "$TMP/outside24/delta-wt" origin/main
  cp "$TMP/outside24/delta-wt/.git" "$root24/copied/.git" || die "copy the .git file failed"
  # shellcheck disable=SC2016  # The $@ and $0 belong to the shim.
  printf '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "$*" == *"worktree list"* && ! -e %q ]]; then : > %q; mv %q %q; mkdir %q; fi\nexec %q "$@"\n' \
    "$shim24/done" "$shim24/done" "$root24" "$root24.moved" "$root24" "$real_git" > "$shim24/git" || die "write the git shim failed"
  chmod +x "$shim24/git" || die "chmod the git shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$shim24:$PATH" PRUNE_IDLE_HOURS=0 bash "$SCRIPT" "$root24" 2>"$TMP/err.$RUN_SEQ")"; RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "24. a root replaced after a discovery with no repository is exit 1, no JSON"
  if (( RC == 1 )) && [[ -e "$shim24/done" && -z "$OUT" && "$ERRTEXT" == *"was replaced"* ]]; then
    pass; else fail "replaced root, no repos: rc=$RC out=$OUT err=$ERRTEXT"; fi

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
