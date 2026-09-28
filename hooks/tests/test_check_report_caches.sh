#!/usr/bin/env bash
# Outcome-based tests for hooks/check-report-caches.sh.
#
# Each case builds its own XDG state root through the fixture builder of the
# owner script's suite (skills/herdr-foreman/tests/test_prune_report_caches.py),
# ages every file to its fixed time and runs the hook with REPORT_CACHES_NOW,
# never the wall clock (rules/testing-standards.md Determinism). The owner
# script's decisions have their own suite; these cases cover what the hook
# adds: it runs the script live and reports the reclaimed bytes, stays silent
# with nothing to report, never deletes in a worker session or under tessl,
# and turns every failure into one status line.
#
# Covers:
#   1. Idle caches          -> removed; one status naming the count.
#   2. Nothing to remove    -> a directory holding evidence alone: silent.
#   3. Portable mode        -> nothing removed; the status names the command.
#   4. Worker session       -> HERDR_ENV in a linked worktree: nothing removed,
#                              nothing printed.
#  11. Herdr, main checkout -> HERDR_ENV in a main checkout: nothing removed,
#                              nothing printed.
#   5. Portable, linked     -> nothing removed, nothing printed.
#   6. Unusable ledger      -> a could-not-check line, nothing removed.
#   7. Out of time          -> a stand-in runner reports the timeout; a
#                              could-not-check line naming the budget.
#   8. No python3           -> the fixed could-not-check JSON.
#   9. Session start        -> session-start.sh runs this hook: its status
#                              reaches the merged payload and the caches go.
#  10. Bad budget override  -> a could-not-check line naming the variable;
#                              nothing removed.
#
# Every case builds its own fixture; no case reads state another left.
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash hooks/tests/test_check_report_caches.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${HERE}/../check-report-caches.sh"
SUITE_DIR="${HERE}/../../skills/herdr-foreman/tests"
PASS=0
FAIL=0
TMP=""

#: FIXTURE_TIME + 25h in the owner suite: every fixture is idle.
NOW=1577926800

die() { echo "fatal: $*" >&2; exit 2; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() {
  if [[ -n "$TMP" ]] && ! rm -rf "$TMP"; then echo "warn: could not remove $TMP" >&2; fi
  return 0
}

# Build a state root under <case>: a ledger naming one reports directory full
# of caches and evidence (evidence alone with `nocache`), all idle. Prints the
# reports directory.
build() { # <case> [corrupt|nocache]
  python3 - "$SUITE_DIR" "$1" "${2:-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from test_prune_report_caches import Fixture, age
fx = Fixture(sys.argv[2])
top = fx.reports(caches=()) if sys.argv[3] == "nocache" else fx.reports()
fx.save()
if sys.argv[3] == "corrupt":
    fx.state.write_text("{not json")
age(top)
print(top)
PY
}

# Run the hook from <cwd> against <case>'s state root; sets OUT and RC.
run_hook() { # <case> <cwd> [env assignments...]
  local case="$1" cwd="$2"; shift 2
  RC=0
  OUT="$(cd "$cwd" && env -u HERDR_ENV -u SESSION_START_MODE XDG_STATE_HOME="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$case")/state" \
    REPORT_CACHES_NOW="$NOW" "$@" bash "$HOOK" </dev/null 2>"$TMP/err")" || RC=$?
}

context() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])["additionalContext"])' "$1"; }

main() {
  TMP="$(mktemp -d)" || die "mktemp -d failed"
  trap cleanup EXIT
  local top case plain

  plain="$TMP/plain"
  mkdir -p "$plain" || die "mkdir $plain failed"

  echo "1. idle caches are removed and reported"
  case="$TMP/c1"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$plain"
  if [[ "$RC" == 0 && ! -e "$top/developer-evidence/venv" && -f "$top/report.md" ]] \
      && [[ "$(context "$OUT")" == "Session-start status — removed 8 regenerable build cache directories"* ]]; then pass
  else fail "case 1: rc=$RC out=$OUT err=$(cat "$TMP/err")"; fi

  echo "2. evidence alone has nothing to report"
  case="$TMP/c2"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case" nocache)" || die "fixture build failed"
  run_hook "$case" "$plain"
  if [[ "$RC" == 0 && -z "$OUT" && -f "$top/report.md" ]]; then pass; else fail "case 2: rc=$RC out=$OUT"; fi

  echo "3. portable mode removes nothing and names the command"
  case="$TMP/c3"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$plain" SESSION_START_MODE=portable
  if [[ "$RC" == 0 && -d "$top/developer-evidence/venv" ]] \
      && [[ "$(context "$OUT")" == *"nothing was removed under tessl. Run \`python3 "*"prune-report-caches.py\` to remove them."* ]]; then pass
  else fail "case 3: rc=$RC out=$OUT"; fi

  local repo="$TMP/repo" wt="$TMP/wt"
  if ! { git init -q -b main "$repo" && git -C "$repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init \
      && git -C "$repo" worktree add -q "$wt" -b side; } 2>"$TMP/git.err"; then
    die "git fixture failed: $(cat "$TMP/git.err")"
  fi

  echo "4. a worker session in a linked worktree deletes nothing"
  case="$TMP/c4"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$wt" HERDR_ENV=
  if [[ "$RC" == 0 && -z "$OUT" && -d "$top/developer-evidence/venv" ]]; then pass
  else fail "case 4: rc=$RC out=$OUT"; fi

  echo "5. portable mode in a linked worktree deletes nothing"
  case="$TMP/c5"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$wt" SESSION_START_MODE=portable
  if [[ "$RC" == 0 && -z "$OUT" && -d "$top/developer-evidence/venv" ]]; then pass
  else fail "case 5: rc=$RC out=$OUT"; fi

  echo "6. an unusable ledger is a could-not-check line"
  case="$TMP/c6"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case" corrupt)" || die "fixture build failed"
  run_hook "$case" "$plain"
  if [[ "$RC" == 0 && -d "$top/developer-evidence/venv" ]] \
      && [[ "$(context "$OUT")" == "Session-start status — could not prune build caches from Herdr reports directories: "* ]]; then pass
  else fail "case 6: rc=$RC out=$OUT"; fi

  echo "7. a spent budget is a could-not-check line"
  local stand="$TMP/stand"
  mkdir -p "$stand/hooks" "$stand/skills/herdr-foreman" || die "mkdir failed"
  cp "$HOOK" "$stand/hooks/" || die "cp hook failed"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 124' > "$stand/skills/herdr-foreman/bounded-run.sh" || die "stub failed"
  printf '%s\n' 'raise SystemExit(0)' > "$stand/skills/herdr-foreman/prune-report-caches.py" || die "stub failed"
  RC=0
  OUT="$(cd "$plain" && env -u HERDR_ENV -u SESSION_START_MODE bash "$stand/hooks/check-report-caches.sh" </dev/null 2>"$TMP/err")" || RC=$?
  if [[ "$RC" == 0 && "$(context "$OUT")" == *"ran past its time budget"* ]]; then pass
  else fail "case 7: rc=$RC out=$OUT"; fi

  echo "8. no python3 prints the fixed status"
  local shims="$TMP/shims"
  mkdir -p "$shims" || die "mkdir failed"
  ln -s "$(command -v git)" "$shims/git" || die "ln git failed"
  RC=0
  OUT="$(cd "$plain" && env -u HERDR_ENV -u SESSION_START_MODE PATH="$shims" "$BASH" "$HOOK" </dev/null 2>"$TMP/err")" || RC=$?
  if [[ "$RC" == 0 && "$(context "$OUT")" == *"python3 is not on PATH"* ]]; then pass
  else fail "case 8: rc=$RC out=$OUT"; fi

  echo "9. session-start runs this hook"
  case="$TMP/c9"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  RC=0
  OUT="$(cd "$plain" && env -u HERDR_ENV -u SESSION_START_MODE -u TESSL_AGENT -u SESSION_START_HOOKS \
    XDG_STATE_HOME="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$case")/state" \
    REPORT_CACHES_NOW="$NOW" bash "${HERE}/../session-start.sh" </dev/null 2>"$TMP/err")" || RC=$?
  if [[ "$RC" == 0 && ! -e "$top/developer-evidence/venv" ]] && python3 -c '
import json, sys
ctx = json.loads(sys.argv[1])["hookSpecificOutput"]["additionalContext"]
sys.exit(0 if "removed 8 regenerable build cache directories" in ctx else 1)' "$OUT"; then pass
  else fail "case 9: rc=$RC out=$OUT"; fi

  echo "10. a bad budget override is a could-not-check line"
  case="$TMP/c10"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$plain" REPORT_CACHES_BUDGET_SEC=soon
  if [[ "$RC" == 0 && -d "$top/developer-evidence/venv" ]] \
      && [[ "$(context "$OUT")" == *"REPORT_CACHES_BUDGET_SEC is not a positive whole number"* ]]; then pass
  else fail "case 10: rc=$RC out=$OUT"; fi

  echo "11. a Herdr session in a main checkout deletes nothing"
  case="$TMP/c11"; mkdir -p "$case" || die "mkdir failed"
  top="$(build "$case")" || die "fixture build failed"
  run_hook "$case" "$repo" HERDR_ENV=1
  if [[ "$RC" == 0 && -z "$OUT" && -d "$top/developer-evidence/venv" ]]; then pass
  else fail "case 11: rc=$RC out=$OUT"; fi

  echo ""
  echo "passed: $PASS, failed: $FAIL"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
