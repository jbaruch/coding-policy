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
: "${FIXTURE_OPEN_PR:=0}"        # 1 = an open PR already exists for the adopted branch
: "${FIXTURE_LSREMOTE_FAIL:=0}"  # 1 = git ls-remote fails (network/auth)
: "${FIXTURE_PRLIST_FAIL:=0}"    # 1 = gh pr list fails (auth/API)
: "${FIXTURE_BODY:=}"            # original PR body text
: "${FIXTURE_COMMENT_FAIL:=0}"        # 1 = gh pr comment fails
: "${FIXTURE_COMMENT_EXISTS:=0}"      # 1 = original PR already links the adopted URL
: "${FIXTURE_COMMENTSREAD_FAIL:=0}"   # 1 = gh pr view --json comments fails

gh() {
  case "$1 $2" in
    "pr view")
      if [[ "$*" == *"--json comments"* ]]; then
        if [ "$FIXTURE_COMMENTSREAD_FAIL" = "1" ]; then return 6; fi
        if [ "$FIXTURE_COMMENT_EXISTS" = "1" ]; then
          printf '{"comments":[{"body":"Adopted into the base repo as https://github.com/owner/blog-writer/pull/99"}]}\n'
        else
          printf '{"comments":[]}\n'
        fi
      else
        cat <<JSON
{"number":${3},"isCrossRepository":${FIXTURE_IS_FORK},"headRefName":"feat/cool-thing",
 "headRepositoryOwner":{"login":"contributor"},"headRepository":{"name":"blog-writer"},
 "author":{"login":"contributor"},"title":"feat: a cool thing","url":"https://github.com/owner/blog-writer/pull/${3}",
 "state":"${FIXTURE_STATE}","baseRefName":"main","body":"${FIXTURE_BODY}"}
JSON
      fi
      ;;
    "pr checkout") return 0 ;;
    "pr create")
      if [ -n "${BODY_CAPTURE:-}" ]; then
        local prev="" b=""
        for a in "$@"; do
          if [ "$prev" = "--body" ]; then b="$a"; fi
          prev="$a"
        done
        printf '%s' "$b" > "$BODY_CAPTURE"
      fi
      printf 'https://github.com/owner/blog-writer/pull/99\n' ;;
    "pr comment") if [ "$FIXTURE_COMMENT_FAIL" = "1" ]; then return 5; fi; return 0 ;;
    "pr list")
      if [ "$FIXTURE_PRLIST_FAIL" = "1" ]; then return 4; fi
      if [ "$FIXTURE_OPEN_PR" = "1" ]; then printf 'https://github.com/owner/blog-writer/pull/42\n'; else printf '\n'; fi ;;
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
      if [ "$FIXTURE_LSREMOTE_FAIL" = "1" ]; then return 2; fi
      if [ "$FIXTURE_BRANCH_EXISTS" = "1" ]; then printf 'deadbeef\trefs/heads/adopted\n'; fi
      return 0 ;;
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

# ---- extract_author_model_line (pure) ------------------------------------
eq "$(extract_author_model_line $'intro\n**Author-Model:** gpt-5.4\nmore')" "**Author-Model:** gpt-5.4" "extracts bold Author-Model line"
eq "$(extract_author_model_line $'no declaration here')" "" "no Author-Model line → empty"

# ---- argument validation -------------------------------------------------
run_main >/dev/null 2>&1;     rc_is "$?" 2 "no arg → exit 2"
run_main abc >/dev/null 2>&1; rc_is "$?" 2 "non-numeric → exit 2"
run_main 0 >/dev/null 2>&1;   rc_is "$?" 2 "zero → exit 2"

# ---- not-a-fork refusal --------------------------------------------------
FIXTURE_IS_FORK=false run_main 6 >/dev/null 2>&1; rc_is "$?" 3 "same-repo PR → exit 3"

# ---- non-OPEN refusal ----------------------------------------------------
FIXTURE_STATE=MERGED run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "non-OPEN fork PR → exit 1"

# ---- ls-remote failure (network/auth) → exit 1, not silent fresh-adoption -
FIXTURE_LSREMOTE_FAIL=1 run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "ls-remote failure → exit 1"

# ---- gh pr list failure on existing branch → exit 1, not false recovery --
FIXTURE_BRANCH_EXISTS=1 FIXTURE_PRLIST_FAIL=1 run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "gh pr list failure → exit 1"

# ---- pointer-comment failure is fatal (not a warning) --------------------
FIXTURE_COMMENT_FAIL=1 run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "pointer-comment failure → exit 1"

# ---- comments-read probe failure is fatal (not "no comment") -------------
FIXTURE_COMMENTSREAD_FAIL=1 run_main 6 >/dev/null 2>&1; rc_is "$?" 1 "comments-read failure → exit 1"

# ---- pointer comment is idempotent (already linked → no re-post) ----------
out=$(FIXTURE_COMMENT_EXISTS=1 FIXTURE_COMMENT_FAIL=1 run_main 6 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] && [ "$(jq -r '.state' <<<"$out")" = "adopted" ]; then
  ok "existing pointer link → idempotent skip, adoption succeeds"
else
  bad "existing pointer link → idempotent skip (got: $out rc=$rc)"
fi

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

# ---- Author-Model carry-over from a body-only declaration ----------------
BODY_CAP=$(mktemp)
FIXTURE_BODY='**Author-Model:** gpt-5.4' BODY_CAPTURE="$BODY_CAP" run_main 6 >/dev/null 2>&1
if head -1 "$BODY_CAP" | grep -q '^\*\*Author-Model:\*\* gpt-5.4$'; then
  ok "carries Author-Model line from original PR body into adopted PR body"
else
  bad "carries Author-Model line from original PR body (captured: $(cat "$BODY_CAP"))"
fi
rm -f "$BODY_CAP"

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
