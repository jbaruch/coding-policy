#!/usr/bin/env bash
# Outcome-based tests for stop-handoff-hygiene.sh.
#
# The hook shells out to real git, so tests build real local repos (a bare
# "origin" plus working clones) and drive git offline — no network, fully
# deterministic. Diagnostics scenarios stub shellcheck/pyright on PATH so the
# changed-set gate is exercised without depending on the real engines.
#
# Each scenario builds its OWN origin so scenarios share no mutable state and run
# in any order (rules/testing-standards.md Independence). The harness drops
# `set -e` to aggregate results, so every fixture-setup command is checked
# explicitly and aborts with a fatal diagnostic (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Covers:
#   1. Loop guard      -> stop_hook_active:true allows even with leftovers.
#   2. Clean repo      -> allow (no stdout), exit 0.
#   3. Spent branch    -> block; reason names the branch and the owner script.
#   4. Spent worktree  -> block; reason names the worktree and the owner script.
#      4a. Dirty, unpushed, locked, not yet idle -> never blocking; the idle
#          dirty and unpushed ones are reported on stderr.
#      4b. Owner script failure (no origin) -> warn, block nothing.
#      4c. Herdr worker session -> no worktree finding; diagnostics still gate.
#          An empty HERDR_ENV still marks the worker.
#      4d. The foreman's session -> blocks, naming the round's sweep, never
#          prune-worktrees.sh (case 4 names prune-worktrees.sh outside Herdr).
#          An empty HERDR_ENV still marks the foreman.
#      4e. A shared checkout whose name ends in a newline -> still blocks.
#      4f. A finding naming a newline-bearing path -> reported whole.
#      4g. HERDR_ENV set, role probes fail (git shim) -> no worktree check,
#          a skip report naming the diagnostic command; diagnostics still gate.
#      4h. A hooks directory whose name ends in a newline -> still reaches
#          the owner scripts and blocks (#487).
#   5. Dirty tree only -> allow (report-only, not a block).
#   6. Diag finding    -> block; changed uncommitted .sh with a failing engine.
#   7. Diag clean      -> changed uncommitted .sh, engines clean -> no diag block.
#   8. No jq           -> fail-open allow, exit 0.
#   9. Not a repo      -> allow, exit 0.
#  10. Engine absent   -> block with install guidance (changed .sh, no shellcheck).
#
# Run: bash hooks/tests/test_stop_handoff_hygiene.sh
set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/stop-handoff-hygiene.sh"

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
g() { git "$@"; }

# mk_origin <prefix>: sets globals BARE, SEED (fresh, independent origin with c1).
mk_origin() {
  local prefix="$1"
  BARE="$TMP/${prefix}.git"; SEED="$TMP/${prefix}-seed"
  g init -q --bare -b main "$BARE"                || die "mk_origin: init bare failed"
  g clone -q "$BARE" "$SEED" 2>"$TMP/clone.err"  || die "mk_origin: clone seed failed: $(cat "$TMP/clone.err")"
  g -C "$SEED" symbolic-ref HEAD refs/heads/main  || die "mk_origin: symbolic-ref failed"
  printf 'c1\n' > "$SEED/f"                        || die "mk_origin: write f failed"
  g -C "$SEED" add f                              || die "mk_origin: add failed"
  g -C "$SEED" commit -q -m c1                    || die "mk_origin: commit failed"
  g -C "$SEED" push -q origin main                || die "mk_origin: push failed"
}

clone_from() { g clone -q "$1" "$2" || die "clone_from: $1 -> $2 failed"; }

# Make a branch whose upstream is gone: push it, delete it on origin, prune.
# Run from the given worktree dir. <dir> <branch>
make_gone_branch() {
  local dir="$1" br="$2"
  g -C "$dir" push -q -u origin "$br"     || die "make_gone_branch: push $br failed"
  g -C "$dir" push -q origin --delete "$br" || die "make_gone_branch: delete $br failed"
  g -C "$dir" fetch -q --prune            || die "make_gone_branch: prune failed"
}

mk_stub_bin() { # <dir> <sc_rc> <py_rc>
  mkdir -p "$1" || die "mk_stub_bin: mkdir $1 failed"
  printf '#!/usr/bin/env bash\nexit %s\n' "$2" > "$1/shellcheck" || die "stub shellcheck failed"
  printf '#!/usr/bin/env bash\nexit %s\n' "$3" > "$1/pyright"    || die "stub pyright failed"
  chmod +x "$1/shellcheck" "$1/pyright" || die "chmod stubs failed"
}

mk_pyright_probe() { # <path>
  cat > "$1" <<'PROBE' || die "could not write Pyright environment probe"
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$0" "$@" > "${PYRIGHT_PROBE_LOG:?}"
if [[ -n "${PYRIGHT_REQUIRED_INTERPRETER:-}" ]]; then
  if [[ "${1:-}" != --pythonpath || "${2:-}" != "$PYRIGHT_REQUIRED_INTERPRETER" ]]; then
    echo 'reportMissingImports: interpreter does not expose the project dependency'
    exit 1
  fi
  shift 2
elif [[ "${1:-}" == --pythonpath ]]; then
  echo 'unexpected override of default/config interpreter'
  exit 1
fi
if [[ $# != 1 || "$1" != 'changed file.py' || ! -f "$1" ]]; then
  echo 'changed Python file was not resolved from the repository root'
  exit 1
fi
if [[ -n "${PYRIGHT_PROBE_FINDING:-}" ]]; then
  printf '%s\n' "$PYRIGHT_PROBE_FINDING"
  exit 1
fi
PROBE
  chmod +x "$1" || die "could not enable Pyright environment probe"
}

mk_python_probe() { # <path>
  mkdir -p "${1%/*}" || die "could not create environment directory"
  printf '#!/bin/sh\nexit 0\n' > "$1" || die "could not write interpreter fixture"
  chmod +x "$1" || die "could not enable interpreter fixture"
}

# write_role_shim <dir> <case-pattern> <real-git>: a git stand-in that fails
# any invocation carrying an argument matching <case-pattern> and passes every
# other one through to the real git.
write_role_shim() {
  mkdir -p "$1" || die "could not create $1"
  cat > "$1/git" <<SH || die "could not write the git shim in $1"
#!/usr/bin/env bash
set -euo pipefail
for a in "\$@"; do
  case "\$a" in
    $2) echo "shim: role probe refused" >&2; exit 1 ;;
  esac
done
exec $(printf '%q' "$3") "\$@"
SH
  chmod +x "$1/git" || die "could not make the git shim in $1 executable"
}

#: The owner script judges idleness at this instant (2020-01-10T00:00:00Z);
#: an aged fixture is touched to AGED_MTIME, nine days before it.
PRUNE_NOW=1578614400
AGED_MTIME=202001010000

# run_hook <repo> <stop-json> [path] [VAR=value...] -> OUT, RC, ERRTEXT
run_hook() {
  local repo="$1" json="$2" pathspec="${3:-$PATH}"
  shift 2
  (( $# )) && shift
  OUT="$(cd "$repo" && printf '%s' "$json" | env -u HERDR_ENV PATH="$pathspec" WORKTREE_ROOT="$TMP/wt" \
    PRUNE_NOW="$PRUNE_NOW" PRUNE_IDLE_HOURS=24 STOP_PRUNE_BUDGET_SEC=3600 "$@" bash "$HOOK" 2>"$TMP/hook.err")"
  RC=$?
  ERRTEXT="$(cat "$TMP/hook.err")"
}

# Touch a worktree's files and its gitdir's activity files to AGED_MTIME.
age_wt() { # <worktree>
  local gitdir f
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir)" || die "rev-parse --absolute-git-dir failed in $1"
  for f in "$gitdir/HEAD" "$gitdir/index" "$gitdir/logs/HEAD"; do
    if [[ -e "$f" ]]; then touch -t "$AGED_MTIME" "$f" || die "touch $f failed"; fi
  done
  find "$1" -path "$1/.git" -prune -o -exec touch -h -t "$AGED_MTIME" {} + || die "touch the files of $1 failed"
}

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
reason_has() { printf '%s' "$OUT" | jq -e --arg re "$1" '.reason | test($re)' >/dev/null; }

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
  command -v jq  >/dev/null || die "jq required for these tests"
  command -v git >/dev/null || die "git required for these tests"
  [[ -f "$HOOK" && -r "$HOOK" ]] || die "hook not found/readable at $HOOK"

  TMP="$(mktemp -d -t stop-hygiene-test.XXXXXX)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "could not resolve fixture root"
  trap cleanup EXIT
  export HOME="$TMP/home"; mkdir -p "$HOME" || die "could not create isolated HOME"
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  # Every commit and reflog entry is dated nine days before PRUNE_NOW.
  export GIT_AUTHOR_DATE="2020-01-01T00:00:00Z" GIT_COMMITTER_DATE="2020-01-01T00:00:00Z"
  mkdir -p "$TMP/wt" || die "could not create the worktree root"
  write_fake_lsof "$TMP/fake-lsof/lsof"
  export PRUNE_LSOF="$TMP/fake-lsof/lsof" FAKE_LSOF_CWDS="$TMP/fake-lsof/cwds"

  FAIL=0; PASS=0

  # 1. loop guard: active:true allows even with a gone branch present.
  mk_origin o1; clone_from "$BARE" "$TMP/r1"
  g -C "$TMP/r1" switch -qc feat/foo || die "r1 branch failed"
  make_gone_branch "$TMP/r1" feat/foo
  g -C "$TMP/r1" switch -q main || die "r1 switch main failed"
  run_hook "$TMP/r1" '{"stop_hook_active":true}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "loop guard: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 2. clean repo -> allow.
  mk_origin o2; clone_from "$BARE" "$TMP/r2"
  run_hook "$TMP/r2" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "clean: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 3. a merged branch the owner script would delete -> block naming it and
  #    the owner script.
  mk_origin o3; clone_from "$BARE" "$TMP/r3"
  g -C "$TMP/r3" switch -qc feat/bar || die "r3 branch failed"
  make_gone_branch "$TMP/r3" feat/bar
  g -C "$TMP/r3" switch -q main || die "r3 switch main failed"
  run_hook "$TMP/r3" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 ]] && reason_has "branch feat/bar" && reason_has "prune-worktrees.sh" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]] && g -C "$TMP/r3" show-ref -q --verify refs/heads/feat/bar; then
    pass; else fail "spent branch: expected a block naming feat/bar, nothing deleted, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4. an idle clean worktree origin holds -> block naming it; the hook
  #    removes nothing itself.
  mk_origin o4; clone_from "$BARE" "$TMP/r4"
  g -C "$TMP/r4" worktree add -q "$TMP/wt/r4-wt" -b feat/wt || die "r4 worktree add failed"
  age_wt "$TMP/wt/r4-wt"
  run_hook "$TMP/r4" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 ]] && reason_has "worktree .*r4-wt \\(feat/wt\\)" && reason_has "prune-worktrees.sh" && ! reason_has "sweep-worktrees.sh" \
     && [[ -d "$TMP/wt/r4-wt" ]]; then
    pass; else fail "spent worktree: expected a block naming r4-wt, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4a. work that exists nowhere else never blocks: an idle dirty and an idle
  #     unpushed worktree are reported; a locked one and one inside the idle
  #     window are neither.
  mk_origin o4a; clone_from "$BARE" "$TMP/r4a"
  g -C "$TMP/r4a" worktree add -q "$TMP/wt/r4a-dirty" -b review/dirty || die "r4a dirty add failed"
  printf 'notes\n' > "$TMP/wt/r4a-dirty/notes.txt" || die "r4a write failed"
  age_wt "$TMP/wt/r4a-dirty"
  g -C "$TMP/r4a" worktree add -q "$TMP/wt/r4a-ahead" -b feat/ahead || die "r4a ahead add failed"
  printf 'new\n' > "$TMP/wt/r4a-ahead/g" || die "r4a write g failed"
  g -C "$TMP/wt/r4a-ahead" add g || die "r4a add failed"
  g -C "$TMP/wt/r4a-ahead" commit -q -m ahead || die "r4a commit failed"
  age_wt "$TMP/wt/r4a-ahead"
  g -C "$TMP/r4a" worktree add -q "$TMP/wt/r4a-locked" -b review/locked || die "r4a locked add failed"
  age_wt "$TMP/wt/r4a-locked"
  g -C "$TMP/r4a" worktree lock "$TMP/wt/r4a-locked" || die "r4a lock failed"
  g -C "$TMP/r4a" worktree add -q "$TMP/wt/r4a-fresh" -b review/fresh || die "r4a fresh add failed"
  run_hook "$TMP/r4a" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]] \
     && [[ "$ERRTEXT" == *"r4a-dirty (review/dirty), dirty"* && "$ERRTEXT" == *"r4a-ahead (feat/ahead), unpushed"* ]] \
     && [[ "$ERRTEXT" != *r4a-locked* && "$ERRTEXT" != *r4a-fresh* ]]; then
    pass; else fail "operator's work: expected reports only, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4b. an owner script that cannot decide (no origin) blocks nothing and
  #     says why.
  mk_origin o4b; clone_from "$BARE" "$TMP/r4b"
  g -C "$TMP/r4b" worktree add -q "$TMP/wt/r4b-wt" -b review/nobase || die "r4b worktree add failed"
  age_wt "$TMP/wt/r4b-wt"
  g -C "$TMP/r4b" remote remove origin || die "r4b remote remove failed"
  run_hook "$TMP/r4b" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" && "$ERRTEXT" == *"worktree check could not run"* ]]; then
    pass; else fail "owner failure: expected a warning and no block, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4c. The spent worktree of case 4, seen from INSIDE a linked worktree with
  # HERDR_ENV set: a worker session. Removal is the foreman's, so no worktree
  # finding; the worker's own changed files still gate.
  g -C "$TMP/r4" worktree add -q "$TMP/wt/r4-worker" -b review/worker || die "r4 worker add failed"
  run_hook "$TMP/wt/r4-worker" '{"stop_hook_active":false}' "$PATH" HERDR_ENV=1
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "worker session: expected silence, got RC=$RC OUT=$OUT"; fi
  # shellcheck disable=SC2016  # The literal `$x` IS the fixture: the hook's
  # own shellcheck run has to find something to report.
  printf 'if [ $x = 1 ]; then :; fi\n' > "$TMP/wt/r4-worker/bad.sh" || die "r4-worker bad.sh failed"
  run_hook "$TMP/wt/r4-worker" '{"stop_hook_active":false}' "$PATH" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && reason_has "shellcheck findings" && ! reason_has "prune-worktrees"; then
    pass; else fail "worker diagnostics: expected a diagnostics block without the worktree finding, got RC=$RC OUT=$OUT"; fi
  rm -f "$TMP/wt/r4-worker/bad.sh" || die "r4-worker cleanup failed"

  # HERDR_ENV set but empty is still a Herdr session (rules/agent-team-operation.md
  # Two Modes): the worker's worktree finding stays suppressed.
  run_hook "$TMP/wt/r4-worker" '{"stop_hook_active":false}' "$PATH" HERDR_ENV=
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "empty HERDR_ENV worker session: expected silence, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4e. A shared checkout whose name ends in a newline still reaches the
  #     owner script whole: the inventory is read NUL-framed.
  local nl=$'\n'
  if mkdir "$TMP/r4e-nl${nl}" 2>"$TMP/nl.err"; then
    rmdir "$TMP/r4e-nl${nl}" || die "rmdir the newline probe failed"
    mk_origin o4e; clone_from "$BARE" "$TMP/r4e-nl${nl}"
    g -C "$TMP/r4e-nl${nl}" worktree add -q "$TMP/wt/r4e-wt" -b review/nl || die "r4e worktree add failed"
    age_wt "$TMP/wt/r4e-wt"
    run_hook "$TMP/r4e-nl${nl}" '{"stop_hook_active":false}'
    if [[ $RC -eq 0 ]] && reason_has "r4e-wt"; then
      pass; else fail "newline-ending shared checkout: expected the worktree block, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  else
    echo "4e. skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl.err"))" >&2
  fi

  # 4h. A hooks directory whose name ends in a newline still reaches the owner
  #     scripts: a `$(dirname ...)` capture drops the newline and looks beside
  #     a directory that does not exist (#487). Its own fixture: no case state.
  if mkdir "$TMP/probe4h${nl}" 2>"$TMP/nl4h.err"; then
    rmdir "$TMP/probe4h${nl}" || die "rmdir the newline probe failed"
    mk_origin o4h; clone_from "$BARE" "$TMP/r4h"
    g -C "$TMP/r4h" worktree add -q "$TMP/wt/r4h-wt" -b feat/r4h || die "r4h worktree add failed"
    age_wt "$TMP/wt/r4h-wt"
    local stage4h="$TMP/stage4h" real_hook4h="$HOOK"
    mkdir -p "$stage4h/hooks${nl}" "$stage4h/skills/herdr-foreman" || die "mkdir the 4h stage failed"
    cp "$real_hook4h" "$stage4h/hooks${nl}/" || die "stage the hook failed"
    cp "$(dirname "$real_hook4h")/../skills/herdr-foreman/prune-worktrees.sh" \
      "$(dirname "$real_hook4h")/../skills/herdr-foreman/bounded-run.sh" "$stage4h/skills/herdr-foreman/" \
      || die "stage the owner scripts failed"
    HOOK="$stage4h/hooks${nl}/$(basename "$real_hook4h")"
    run_hook "$TMP/r4h" '{"stop_hook_active":false}'
    HOOK="$real_hook4h"
    if [[ $RC -eq 0 ]] && reason_has "worktree .*r4h-wt \\(feat/r4h\\)"; then
      pass; else fail "newline-ending hooks dir: expected the worktree block, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  else
    echo "4h. skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl4h.err"))" >&2
  fi

  # 4f. A finding whose text carries a newline-bearing path arrives whole: an
  #     unpushed branch's push command names a shared checkout whose name
  #     ends in a newline.
  if mkdir "$TMP/r4f-nl${nl}" 2>"$TMP/nl.err"; then
    rmdir "$TMP/r4f-nl${nl}" || die "rmdir the newline probe failed"
    mk_origin o4f; clone_from "$BARE" "$TMP/r4f-nl${nl}"
    g -C "$TMP/r4f-nl${nl}" switch -qc feat/nl-local || die "r4f branch failed"
    printf 'local\n' > "$TMP/r4f-nl${nl}/l" || die "r4f write failed"
    g -C "$TMP/r4f-nl${nl}" add l || die "r4f add failed"
    g -C "$TMP/r4f-nl${nl}" commit -q -m local || die "r4f commit failed"
    g -C "$TMP/r4f-nl${nl}" switch -q main || die "r4f switch main failed"
    run_hook "$TMP/r4f-nl${nl}" '{"stop_hook_active":false}'
    if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERRTEXT" == *"Branch left for the operator: feat/nl-local"*"r4f-nl${nl}' push -u origin feat/nl-local"* ]]; then
      pass; else fail "newline-bearing finding: expected the whole push command, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  else
    echo "4f. skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl.err"))" >&2
  fi

  # 4d. The foreman's own session (main checkout) still blocks with HERDR_ENV
  # set: the suppression keys on being in a linked worktree, not on Herdr alone.
  run_hook "$TMP/r4" '{"stop_hook_active":false}' "$PATH" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && reason_has "r4-wt" && reason_has "sweep-worktrees.sh" && ! reason_has "prune-worktrees.sh"; then
    pass; else fail "foreman session: expected the worktree block naming the sweep, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  run_hook "$TMP/r4" '{"stop_hook_active":false}' "$PATH" HERDR_ENV=
  if [[ $RC -eq 0 ]] && reason_has "r4-wt" && reason_has "sweep-worktrees.sh" && ! reason_has "prune-worktrees.sh"; then
    pass; else fail "empty HERDR_ENV foreman session: expected the worktree block naming the sweep, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4g. HERDR_ENV set and the role probes fail (a git shim on PATH refuses
  #     them): the role is unknown, so the foreman-only worktree check never
  #     runs, even from a main checkout holding a spent worktree. The hook says
  #     why and names the diagnostic command. Its own origin, checkout and
  #     worktree keep it independent of every other case.
  mk_origin o4g; clone_from "$BARE" "$TMP/r4g"
  g -C "$TMP/r4g" worktree add -q "$TMP/wt/r4g-wt" -b feat/r4g || die "r4g worktree add failed"
  age_wt "$TMP/wt/r4g-wt"
  local realgit probe
  realgit="$(command -v git)" || die "no git on PATH"
  write_role_shim "$TMP/shim4g-both" '--absolute-git-dir|--git-common-dir' "$realgit"
  write_role_shim "$TMP/shim4g-common" '--git-common-dir' "$realgit"
  for probe in both common; do
    run_hook "$TMP/r4g" '{"stop_hook_active":false}' "$TMP/shim4g-$probe:$PATH" HERDR_ENV=1
    if [[ $RC -eq 0 && -z "$OUT" ]] \
       && [[ "$ERRTEXT" == *"Worktree and branch check skipped"* ]] \
       && [[ "$ERRTEXT" == *"\`git rev-parse --absolute-git-dir --path-format=absolute --git-common-dir\`"* ]] \
       && [[ "$ERRTEXT" != *r4g-wt* && "$ERRTEXT" != *"worktree check could not"* ]]; then
      pass; else fail "unknown role ($probe probe fails): expected no worktree check and a skip report, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  done
  # The unknown role still gates the session's own changed files. A stubbed
  # engine that reports a finding keeps the case host-independent.
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r4g/new.sh" || die "r4g new.sh failed"
  mk_stub_bin "$TMP/r4g-bin" 1 0
  run_hook "$TMP/r4g" '{"stop_hook_active":false}' "$TMP/shim4g-both:$TMP/r4g-bin:$PATH" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && reason_has "shellcheck findings" && reason_has "Worktree and branch check skipped" && ! reason_has "r4g-wt"; then
    pass; else fail "unknown role diagnostics: expected a diagnostics block without the worktree finding, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 5. dirty tree only -> allow (report-only).
  mk_origin o5; clone_from "$BARE" "$TMP/r5"
  printf 'dirty\n' >> "$TMP/r5/f" || die "r5 dirty failed"
  run_hook "$TMP/r5" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "dirty only: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 6. changed-set diagnostics finding -> block. Uncommitted .sh + failing engine.
  mk_origin o6; clone_from "$BARE" "$TMP/r6"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r6/new.sh" || die "r6 new.sh failed"
  mk_stub_bin "$TMP/r6-bin" 1 0     # stub engine exits 1 (a finding)
  run_hook "$TMP/r6" '{"stop_hook_active":false}' "$TMP/r6-bin:$PATH"
  if [[ $RC -eq 0 ]] && reason_has "shellcheck findings" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]]; then
    pass; else fail "diag finding: expected block, got RC=$RC OUT=$OUT"; fi

  # 7. changed-set diagnostics clean -> no diagnostics block (dirty tree is only
  #    report-only, so allow). Proves the changed set was linted and passed.
  mk_origin o7; clone_from "$BARE" "$TMP/r7"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r7/new.sh" || die "r7 new.sh failed"
  mk_stub_bin "$TMP/r7-bin" 0 0     # engines clean
  run_hook "$TMP/r7" '{"stop_hook_active":false}' "$TMP/r7-bin:$PATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "diag clean: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 8. no jq -> fail-open allow. Minimal PATH with git/bash but no jq.
  mk_origin o8; clone_from "$BARE" "$TMP/r8"
  local minbin="$TMP/minbin"; mkdir -p "$minbin" || die "minbin mkdir failed"
  local t
  for t in bash git cat env mkdir; do
    local p; p="$(command -v "$t")" || die "no $t on PATH"
    ln -s "$p" "$minbin/$t" || die "symlink $t failed"
  done
  run_hook "$TMP/r8" '{"stop_hook_active":false}' "$minbin"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no jq: expected fail-open allow, got RC=$RC OUT=$OUT"; fi

  # 9. not a repo -> allow.
  mkdir -p "$TMP/notrepo" || die "notrepo mkdir failed"
  run_hook "$TMP/notrepo" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "not a repo: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 10. engine unavailable for a present type -> block with install guidance
  #     (rules/language-diagnostics.md: the gate can't clear findings without the
  #     engine). PATH has git/jq/cat/bash but no shellcheck; a changed .sh forces
  #     the check.
  mk_origin o10; clone_from "$BARE" "$TMP/r10"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r10/new.sh" || die "r10 new.sh failed"
  local engbin="$TMP/engbin"; mkdir -p "$engbin" || die "engbin mkdir failed"
  local u p2
  for u in bash git jq cat mktemp rm; do
    p2="$(command -v "$u")" || die "no $u on PATH"
    ln -s "$p2" "$engbin/$u" || die "symlink $u failed"
  done
  run_hook "$TMP/r10" '{"stop_hook_active":false}' "$engbin"
  if [[ $RC -eq 0 ]] && reason_has "shellcheck is not installed" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]]; then
    pass; else fail "engine unavailable: expected block with install guidance, got RC=$RC OUT=$OUT"; fi

  # 11. Environment selection drives the public hook outcome. Each fixture
  # has a dependency that the probe resolves only with its intended Python.
  local shape repo interpreter engine active_env probe_log
  for shape in active dotvenv venv windows default; do
    mk_origin "py-$shape"
    repo="$TMP/python $shape"; clone_from "$BARE" "$repo"
    printf 'import project_dependency\n' > "$repo/changed file.py" || die "could not write Python fixture"
    mkdir "$repo/nested" "$TMP/bin-$shape" || die "could not create probe directories"
    mk_pyright_probe "$TMP/bin-$shape/pyright"
    active_env=""; interpreter=""; engine="$TMP/bin-$shape/pyright"
    case "$shape" in
      active)
        active_env="$TMP/active environment"
        interpreter="$active_env/bin/python"
        mk_python_probe "$repo/.venv/bin/python"
        mk_python_probe "$repo/venv/bin/python"
        ;;
      dotvenv)
        active_env="$TMP/missing environment"
        interpreter="$repo/.venv/bin/python"
        mk_python_probe "$repo/venv/bin/python"
        ;;
      venv) interpreter="$repo/venv/bin/python" ;;
      windows) interpreter="$repo/.venv/Scripts/python.exe" ;;
      default) ;;
    esac
    if [[ -n "$interpreter" ]]; then mk_python_probe "$interpreter"; fi
    if [[ "$shape" == active || "$shape" == dotvenv || "$shape" == windows ]]; then
      engine="${interpreter%/*}/pyright"
      if [[ "$shape" == windows ]]; then engine+=.exe; fi
      mk_pyright_probe "$engine"
    fi
    probe_log="$TMP/probe-$shape.log"
    VIRTUAL_ENV="$active_env" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" \
      run_hook "$repo/nested" '{"stop_hook_active":false}' "$TMP/bin-$shape:$PATH"
    if [[ $RC -eq 0 && -z "$OUT" && -r "$probe_log" ]] && [[ "$(head -1 "$probe_log")" == "$engine" ]]; then
      pass; else fail "$shape environment must resolve the dependency through the intended engine: RC=$RC OUT=$OUT"; fi

    if [[ "$shape" == dotvenv ]]; then
      # Local engine is sufficient even when PATH contains no Pyright.
      VIRTUAL_ENV="" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" \
        run_hook "$repo" '{"stop_hook_active":false}' "$engbin"
      if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "local Pyright must work without a global installation: OUT=$OUT"; fi
      local finding
      for finding in reportArgumentType reportMissingImports; do
        VIRTUAL_ENV="" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" PYRIGHT_PROBE_FINDING="$finding" \
          run_hook "$repo" '{"stop_hook_active":false}' "$TMP/bin-$shape:$PATH"
        if [[ $RC -eq 0 ]] && reason_has "$finding" && reason_has 'pyright findings'; then
          pass; else fail "environment selection must preserve blocking $finding diagnostics: OUT=$OUT"; fi
      done
    fi
  done

  # No engine in either environment or PATH retains the explicit install gate.
  mk_origin py-missing; repo="$TMP/python missing"; clone_from "$BARE" "$repo"
  printf 'value = 1\n' > "$repo/changed file.py" || die "could not write missing-engine fixture"
  mk_python_probe "$repo/.venv/bin/python"
  VIRTUAL_ENV="" run_hook "$repo" '{"stop_hook_active":false}' "$engbin"
  if [[ $RC -eq 0 ]] && reason_has 'pyright is not installed'; then
    pass; else fail "missing Pyright must retain install guidance: OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
