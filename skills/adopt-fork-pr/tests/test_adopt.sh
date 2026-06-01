#!/usr/bin/env bash
# Tests for adopt.sh. Sources the script (the main() guard prevents auto-run),
# mocks `gh` and `git` as functions dispatching on subcommand, and drives
# scenarios through env vars. Deterministic: no network, no real git/gh.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/adopt.sh"
set +e  # relax errexit in the harness; each main() runs under its own set -e

pass=0; fail=0
ok()    { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad()   { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }
eq()    { if [ "$1" = "$2" ];   then ok "$3"; else bad "$3 (got: $1)"; fi; }
rc_is() { if [ "$1" -eq "$2" ]; then ok "$3"; else bad "$3 (rc=$1)"; fi; }

# ---- mocks ---------------------------------------------------------------
# Scenario knobs (defaults = open fork PR, adopted branch not yet on origin):
: "${FIXTURE_IS_FORK:=true}"
: "${FIXTURE_STATE:=OPEN}"
: "${FIXTURE_BRANCH_EXISTS:=0}"
: "${FIXTURE_OPEN_PR:=0}"   # 1 = an open PR already exists for the adopted branch

gh() {
  case "$1 $2" in
    "pr view")
      cat <<JSON
{"number":${3},"isCrossRepository":${FIXTURE_IS_FORK},"headRefName":"feat/cool-thing",
 "headRepositoryOwner":{"login":"contributor"},"headRepository":{"name":"blog-writer"},
 "author":{"login":"contributor"},"title":"feat: a cool thing","url":"https://github.com/owner/blog-writer/pull/${3}",
 "state":"${FIXTURE_STATE}","baseRefName":"main"}
JSON
      ;;
    "pr checkout") return 0 ;;
    "pr create")  printf 'https://github.com/owner/blog-writer/pull/99\n' ;;
    "pr comment") return 0 ;;
    "pr list")    if [ "$FIXTURE_OPEN_PR" = "1" ]; then printf 'https://github.com/owner/blog-writer/pull/42\n'; else printf '\n'; fi ;;
    *) return 0 ;;
  esac
}

git() {
  case "$*" in
    "rev-parse --is-inside-work-tree") return 0 ;;
    "rev-parse HEAD") printf 'deadbeef\n' ;;
    "diff --quiet"|"diff --cached --quiet") return 0 ;;
    "symbolic-ref --quiet --short HEAD") printf 'main\n' ;;
    "ls-remote --heads origin refs/heads/"*)
      [ "$FIXTURE_BRANCH_EXISTS" = "1" ] && printf 'deadbeef\trefs/heads/adopted\n'; return 0 ;;
    "push origin HEAD:refs/heads/"*) return 0 ;;
    "checkout --quiet "*) return 0 ;;
    *) return 0 ;;
  esac
}

run_main() { ( set -euo pipefail; main "$@" ); }  # subshell: capture exit + stdout

# ---- slugify (pure) ------------------------------------------------------
eq "$(slugify 'feat/framework-md-persona-override')" "feat-framework-md-persona-override" "slugify keeps alnum, folds slash"
eq "$(slugify 'Foo_Bar Baz!!')" "foo-bar-baz" "slugify lowercases and squeezes"
eq "$(slugify '---weird///')" "weird" "slugify trims leading/trailing dashes"

# ---- argument validation -------------------------------------------------
run_main >/dev/null 2>&1;     rc_is "$?" 2 "no arg → exit 2"
run_main abc >/dev/null 2>&1; rc_is "$?" 2 "non-numeric → exit 2"
run_main 0 >/dev/null 2>&1;   rc_is "$?" 2 "zero → exit 2"

# ---- not-a-fork refusal --------------------------------------------------
FIXTURE_IS_FORK=false run_main 6 >/dev/null 2>&1; rc_is "$?" 3 "same-repo PR → exit 3"

# ---- non-OPEN refusal ----------------------------------------------------
FIXTURE_STATE=MERGED run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "non-OPEN fork PR → exit 1"

# ---- happy path ----------------------------------------------------------
out=$(run_main 6 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(jq -r '.state' <<<"$out")" = "adopted" ] \
   && [ "$(jq -r '.adopted_branch' <<<"$out")" = "adopt/pr-6-feat-cool-thing" ] \
   && [ "$(jq -r '.new_pr_url' <<<"$out")" = "https://github.com/owner/blog-writer/pull/99" ] \
   && [ "$(jq -r '.original_pr' <<<"$out")" = "6" ] \
   && [ "$(jq -r '.author' <<<"$out")" = "contributor" ]; then
  ok "happy path → adopted JSON with expected fields"
else
  bad "happy path → adopted JSON with expected fields (got: $out rc=$rc)"
fi

# ---- idempotency: branch on origin AND an open PR exists → no-op ----------
out=$(FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=1 run_main 6 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(jq -r '.state' <<<"$out")" = "already-adopted" ] \
   && [ "$(jq -r '.new_pr_url' <<<"$out")" = "https://github.com/owner/blog-writer/pull/42" ]; then
  ok "branch + open PR → already-adopted no-op with existing URL"
else
  bad "branch + open PR → already-adopted no-op with existing URL (got: $out rc=$rc)"
fi

# ---- partial-run recovery: branch on origin but NO open PR → adopt --------
out=$(FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=0 run_main 6 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(jq -r '.state' <<<"$out")" = "adopted" ] \
   && [ "$(jq -r '.new_pr_url' <<<"$out")" = "https://github.com/owner/blog-writer/pull/99" ]; then
  ok "branch but no PR → recovers by opening the PR"
else
  bad "branch but no PR → recovers by opening the PR (got: $out rc=$rc)"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
