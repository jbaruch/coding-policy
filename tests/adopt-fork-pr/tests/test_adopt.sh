#!/usr/bin/env bash
# Tests for adopt.sh. Sources the script (the main() guard prevents auto-run),
# mocks `gh` and `git` as functions dispatching on subcommand, and drives
# scenarios through env vars. Deterministic: no network, no real gh. The one
# real git read is origin-repo.py's `git remote get-url`, against a throwaway
# `git init` whose origin is configured, never fetched.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../skills/adopt-fork-pr" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/adopt.sh"
set +e  # relax errexit in the harness; each main() runs under its own set -e

pass=0; fail=0

# ---- fixture checkout: origin names owner/blog-writer ----------------------
ORIGIN_REPO=owner/blog-writer
export GH_HOST=enterprise.example.invalid
FIXTURE_DIR=$(mktemp -d) || { echo "fatal: mktemp -d failed" >&2; exit 2; }
fixture_cleanup() {
  if ! rm -rf "$FIXTURE_DIR"; then echo "warn: could not remove $FIXTURE_DIR — remove it by hand" >&2; fi
  return 0
}
trap fixture_cleanup EXIT
: > "$FIXTURE_DIR/gitconfig" || { echo "fatal: cannot write an empty git config" >&2; exit 2; }
# The operator's git config (insteadOf rewrites included) never reaches the fixture.
export GIT_CONFIG_GLOBAL="$FIXTURE_DIR/gitconfig" GIT_CONFIG_NOSYSTEM=1
if ! { command git init -q "$FIXTURE_DIR/repo" \
  && command git -C "$FIXTURE_DIR/repo" remote add origin "https://github.com/${ORIGIN_REPO}.git" \
  && cd "$FIXTURE_DIR/repo"; }; then
  echo "fatal: cannot build the fixture checkout" >&2; exit 2
fi
GH_ARGV="$FIXTURE_DIR/gh-argv"
WRITES="$FIXTURE_DIR/writes"
HEAD_OID=1111111111111111111111111111111111111111
BRANCH=adopt/pr-6-feat-cool-thing
METADATA=$(cat <<'JSON'
{"number":6,"isCrossRepository":true,"headRefName":"feat/cool-thing",
 "headRepositoryOwner":{"login":"contributor"},"headRepository":{"name":"blog-writer"},
 "headRefOid":"1111111111111111111111111111111111111111","author":{"login":"contributor"},
 "title":"**Fix** [guide](https://example.invalid/docs) \"quoted\" `code` ($HOME)","url":"https://github.com/owner/blog-writer/pull/6",
 "state":"OPEN","baseRefName":"main"}
JSON
)
EXPECTED_TITLE="**Fix** [guide](https://example.invalid/docs) \"quoted\" \`code\` (\$HOME)"
EXPECTED_BODY="Adopted from #6 by @contributor (fork contributor/blog-writer).

Carries the contributor's original commits unchanged — authorship is preserved. As a same-repo PR, it gets the policy review.

Original PR: https://github.com/owner/blog-writer/pull/6"
EXPECTED_POINTER="Adopted into the base repo as <new_pr_url> so the policy reviewer can run — fork PRs are skipped by the reviewer's fork-guard. Leaving this PR open; close it whenever you like, it's your call."

ok()    { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad()   { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }
eq()    { if [ "$1" = "$2" ];   then ok "$3"; else bad "$3 (got: $1)"; fi; }
rc_is() { if [ "$1" -eq "$2" ]; then ok "$3"; else bad "$3 (rc=$1)"; fi; }

# ---- mocks ---------------------------------------------------------------
# Scenario knobs (defaults = open fork PR, adopted branch not yet on origin):
: "${FIXTURE_BRANCH_EXISTS:=0}"
: "${FIXTURE_OPEN_PR:=0}"        # 1 = an open PR already exists for the adopted branch
: "${FIXTURE_LSREMOTE_FAIL:=0}"  # 1 = git ls-remote fails (network/auth)
: "${FIXTURE_PRLIST_FAIL:=0}"    # 1 = gh pr list fails (auth/API)
: "${FIXTURE_COMMENT_FAIL:=0}"        # 1 = gh pr comment fails
: "${FIXTURE_COMMENT_EXISTS:=0}"      # 1 = original PR already links the adopted URL
: "${FIXTURE_COMMENTSREAD_FAIL:=0}"   # 1 = gh pr view --json comments fails

# Every call appends one line to GH_ARGV: "bound" or "UNBOUND", then its
# subcommand. Require the validated host and repository even with GH_HOST set.
gh() {
  local prev="" a bound=0
  for a in "$@"; do
    if [ "$prev" = "--repo" ] && [ "$a" = "github.com/$ORIGIN_REPO" ]; then bound=1; fi
    prev="$a"
  done
  if [ "$bound" = 1 ]; then printf 'bound %s %s\n' "$1" "$2"; else printf 'UNBOUND %s %s\n' "$1" "$2"; fi >> "$GH_ARGV"
  if [ "$bound" != 1 ]; then echo "fake gh: expected github.com/$ORIGIN_REPO with GH_HOST=$GH_HOST: $*" >&2; return 9; fi
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
        jq "${FIXTURE_EDIT:-.}" <<<"$METADATA"
      fi
      ;;
    "pr create")
      printf 'create\n' >> "$WRITES"
      printf '%s\0' "$@" > "$FIXTURE_DIR/create"
      printf 'https://github.com/owner/blog-writer/pull/99\n' ;;
    "pr comment")
      printf 'comment\n' >> "$WRITES"
      printf '%s\0' "$@" > "$FIXTURE_DIR/comment"
      if [ "$FIXTURE_COMMENT_FAIL" = "1" ]; then return 5; fi
      return 0 ;;
    "pr list")
      if [ "$FIXTURE_PRLIST_FAIL" = "1" ]; then return 4; fi
      if [ "$FIXTURE_OPEN_PR" = "1" ]; then
        jq -n --arg branch "$BRANCH" --arg oid "$HEAD_OID" --arg base "${FIXTURE_PR_BASE:-main}" \
          '[{url:"https://github.com/owner/blog-writer/pull/42",isCrossRepository:false,
             headRefName:$branch,headRefOid:$oid,baseRefName:$base}]'
      else
        printf '[]\n'
      fi ;;
    *) echo "unexpected gh command: $*" >> "$WRITES"; return 2 ;;
  esac
}

git() {
  case "$*" in
    "rev-parse --is-inside-work-tree") return 0 ;;
    "ls-remote --heads origin refs/heads/"*)
      if [ "$FIXTURE_LSREMOTE_FAIL" = "1" ]; then return 2; fi
      if [ "$FIXTURE_BRANCH_EXISTS" = "1" ]; then printf '%s\trefs/heads/%s\n' "${FIXTURE_REMOTE_OID:-$HEAD_OID}" "$BRANCH"; fi
      return 0 ;;
    "fetch --no-tags origin refs/pull/6/head") echo fetch >> "$WRITES" ;;
    "rev-parse --verify FETCH_HEAD") echo "${FIXTURE_FETCHED_OID:-$HEAD_OID}" ;;
    "push "*)
      printf '%s\0' "$@" > "$FIXTURE_DIR/push"
      echo push >> "$WRITES" ;;
    *) echo "unexpected git command: $*" >> "$WRITES"; return 2 ;;
  esac
}

run_main() { ( set -euo pipefail; main "$@" ); }  # subshell: capture exit + stdout

# Every gh call since the last reset named origin's repository with --repo.
all_gh_bound() {
  [ -s "$GH_ARGV" ] && ! grep -q '^UNBOUND' "$GH_ARGV"
}

# ---- slugify (pure) ------------------------------------------------------
eq "$(slugify 'feat/framework-md-persona-override')" "feat-framework-md-persona-override" "slugify keeps alnum, folds slash"
eq "$(slugify 'Foo_Bar Baz!!')" "foo-bar-baz" "slugify lowercases and squeezes"
eq "$(slugify '---weird///')" "weird" "slugify trims leading/trailing dashes"

# ---- emit_jq_missing: stdout JSON envelope + stderr diagnostic -----------
jqm_err=$(mktemp)
jqm_out=$(emit_jq_missing 2>"$jqm_err")
if [ "$(jq -r '.state' <<<"$jqm_out" 2>/dev/null)" = "error" ] && grep -q 'jq is required' "$jqm_err"; then
  ok "jq-missing emits stdout JSON and stderr diagnostic"
else
  bad "jq-missing emits stdout JSON and stderr diagnostic (out=$jqm_out err=$(cat "$jqm_err" 2>/dev/null))"
fi
rm -f "$jqm_err"

# ---- argument validation -------------------------------------------------
run_main >/dev/null 2>&1;     rc_is "$?" 2 "no arg → exit 2"
run_main abc "$ORIGIN_REPO" "{}" >/dev/null 2>&1; rc_is "$?" 2 "non-numeric → exit 2"
run_main 0 "$ORIGIN_REPO" "{}" >/dev/null 2>&1;   rc_is "$?" 2 "zero → exit 2"

METADATA_PAYLOAD=$(run_main metadata 6 | jq -er .metadata_payload) || exit 1
PREVIEW=$(run_main preview 6 "$ORIGIN_REPO" "$METADATA_PAYLOAD") || { bad "read-only preview"; exit 1; }
adopt() { run_main 6 "$ORIGIN_REPO" "$(jq -er .approved_payload <<<"$PREVIEW")"; }
if [[ ! -s "$WRITES" ]] && jq -e --arg title "$EXPECTED_TITLE" --arg body "$EXPECTED_BODY" --arg pointer "$EXPECTED_POINTER" '
  .preview | .body == $body and .pointer == $pointer and .title == $title
' <<<"$PREVIEW" >/dev/null; then ok "preview renders the messages without writing"; else bad "read-only preview content"; fi

# ---- not-a-fork refusal --------------------------------------------------
FIXTURE_EDIT='.isCrossRepository = false' adopt >/dev/null 2>&1; rc_is "$?" 3 "same-repo PR → exit 3"

# ---- non-OPEN refusal ----------------------------------------------------
FIXTURE_EDIT='.state = "MERGED"' adopt >/dev/null 2>&1; rc_is "$?" 1 "non-OPEN fork PR → exit 1"

# ---- ls-remote failure (network/auth) → exit 1, not silent fresh-adoption -
FIXTURE_LSREMOTE_FAIL=1 adopt >/dev/null 2>&1; rc_is "$?" 1 "ls-remote failure → exit 1"

# ---- gh pr list failure on existing branch → exit 1, not false recovery --
FIXTURE_BRANCH_EXISTS=1 FIXTURE_PRLIST_FAIL=1 adopt >/dev/null 2>&1; rc_is "$?" 1 "gh pr list failure → exit 1"

# ---- pointer-comment failure is fatal (not a warning) --------------------
FIXTURE_COMMENT_FAIL=1 adopt >/dev/null 2>&1; rc_is "$?" 1 "pointer-comment failure → exit 1"

# ---- comments-read probe failure is fatal (not "no comment") -------------
FIXTURE_COMMENTSREAD_FAIL=1 adopt >/dev/null 2>&1; rc_is "$?" 1 "comments-read failure → exit 1"

# ---- pointer comment is idempotent (already linked → no re-post) ----------
out=$(FIXTURE_COMMENT_EXISTS=1 FIXTURE_COMMENT_FAIL=1 adopt 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] && [ "$(jq -r '.state' <<<"$out")" = "adopted" ]; then
  ok "existing pointer link → idempotent skip, adoption succeeds"
else
  bad "existing pointer link → idempotent skip (got: $out rc=$rc)"
fi

# ---- non-GitHub origin: refused before any gh call ------------------------
: > "$GH_ARGV"
command git remote set-url origin https://gitlab.com/owner/blog-writer.git \
  || { echo "fatal: cannot point origin at gitlab" >&2; exit 2; }
adopt >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ] && [ ! -s "$GH_ARGV" ]; then
  ok "non-GitHub origin → exit 1, no gh call"
else
  bad "non-GitHub origin → exit 1, no gh call (rc=$rc calls=$(cat "$GH_ARGV"))"
fi
command git remote set-url origin "git@github.com:${ORIGIN_REPO}.git" \
  || { echo "fatal: cannot point origin back at GitHub" >&2; exit 2; }

# ---- happy path ----------------------------------------------------------
: > "$WRITES"
: > "$GH_ARGV"
out=$(adopt 2>/dev/null); rc=$?
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
if all_gh_bound; then
  ok "happy path → every gh call names origin's repository"
else
  bad "happy path → every gh call names origin's repository (calls: $(cat "$GH_ARGV"))"
fi

eq "$(cat "$WRITES")" $'fetch\npush\ncreate\ncomment' "fresh adoption transfers the approved commit once"
printf '%s\0' push --no-follow-tags "--force-with-lease=refs/heads/$BRANCH:" origin "$HEAD_OID:refs/heads/$BRANCH" > "$FIXTURE_DIR/expected-push"
if cmp "$FIXTURE_DIR/expected-push" "$FIXTURE_DIR/push"; then ok "exact approved push arguments"; else bad "push arguments"; fi
printf '%s\0' pr create --repo "github.com/$ORIGIN_REPO" --base main --head "$BRANCH" --title "$EXPECTED_TITLE" \
  --body "$EXPECTED_BODY" > "$FIXTURE_DIR/expected-create"
if cmp "$FIXTURE_DIR/expected-create" "$FIXTURE_DIR/create"; then ok "exact approved create arguments"; else bad "create arguments"; fi
check_pointer() {
  local url="https://github.com/owner/blog-writer/pull/$1"
  printf '%s\0' pr comment 6 --repo "github.com/$ORIGIN_REPO" --body "${EXPECTED_POINTER/<new_pr_url>/"$url"}" > "$FIXTURE_DIR/expected-comment"
  if cmp "$FIXTURE_DIR/expected-comment" "$FIXTURE_DIR/comment"; then ok "exact pointer to #$1"; else bad "pointer arguments"; fi
}
check_pointer 99

# ---- idempotency: branch on origin AND an open PR exists → no-op ----------
: > "$WRITES"
retry_preview=$(FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=1 run_main preview 6 "$ORIGIN_REPO" "$METADATA_PAYLOAD") || exit 1
retry_url="https://github.com/owner/blog-writer/pull/42"
eq "$(jq -r .preview.pointer <<<"$retry_preview")" "${EXPECTED_POINTER/<new_pr_url>/"$retry_url"}" "retry previews the actual pointer to the existing PR"
out=$(FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=1 PREVIEW="$retry_preview" adopt 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(jq -r '.state' <<<"$out")" = "already-adopted" ] \
   && [ "$(jq -r '.new_pr_url' <<<"$out")" = "https://github.com/owner/blog-writer/pull/42" ]; then
  ok "branch + open PR → already-adopted no-op with existing URL"
else
  bad "branch + open PR → already-adopted no-op with existing URL (got: $out rc=$rc)"
fi

eq "$(cat "$WRITES")" "comment" "valid retry only repairs the pointer"
check_pointer 42

: > "$WRITES"
FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=1 adopt >/dev/null; rc_is "$?" 0 "original consent can resume after PR creation"
eq "$(cat "$WRITES")" "comment" "resumed original consent sends only the pointer"
check_pointer 42

# ---- partial-run recovery: branch on origin but NO open PR → adopt --------
: > "$GH_ARGV"
: > "$WRITES"
out=$(FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=0 adopt 2>/dev/null); rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(jq -r '.state' <<<"$out")" = "adopted" ] \
   && [ "$(jq -r '.new_pr_url' <<<"$out")" = "https://github.com/owner/blog-writer/pull/99" ]; then
  ok "branch but no PR → recovers by opening the PR"
else
  bad "branch but no PR → recovers by opening the PR (got: $out rc=$rc)"
fi
if all_gh_bound; then
  ok "recovery → every gh call names origin's repository"
else
  bad "recovery → every gh call names origin's repository (calls: $(cat "$GH_ARGV"))"
fi

eq "$(cat "$WRITES")" $'create\ncomment' "partial push retry skips transfer"

# Changed or unavailable approval inputs must stop before the first mutation.
refuses() {
  local label="$1" expected_writes="$2" rc
  shift 2
  : > "$WRITES"
  "$@" > "$FIXTURE_DIR/out" 2> "$FIXTURE_DIR/err"; rc=$?
  if [[ $rc != 0 && ! -s "$FIXTURE_DIR/out" && "$(cat "$WRITES")" == "$expected_writes" ]] \
     && grep -q 'fresh preview' "$FIXTURE_DIR/err"; then
    ok "$label"
  else
    bad "$label (rc=$rc writes=$(cat "$WRITES") diagnostic=$(cat "$FIXTURE_DIR/err"))"
  fi
}
for edit in '.title = "edited"' '.author.login = "another"' '.url += "/changed"' '.number = 7' \
  '.headRepositoryOwner.login = "another"' '.headRepository.name = "another"' '.headRefName = "other"' \
  '.headRefOid = "2222222222222222222222222222222222222222"' '.baseRefName = "other"' \
  'del(.headRefOid)' 'del(.headRepository)' 'del(.title)'; do
  FIXTURE_EDIT="$edit" refuses "PR metadata change: $edit" "" adopt
done
refuses "missing approved JSON" "" run_main 6 "$ORIGIN_REPO"
refuses "unavailable approved JSON" "" run_main 6 "$ORIGIN_REPO" ""
command git remote set-url origin https://github.com/other/blog-writer.git || exit 2
refuses "origin changed after consent" "" adopt
command git remote set-url origin "git@github.com:${ORIGIN_REPO}.git" || exit 2
command git config remote.origin.pushurl https://github.com/other/blog-writer.git || exit 2
refuses "origin push target changed after consent" "" adopt
command git config --unset remote.origin.pushurl || exit 2
refuses "edited body in consent" "" run_main 6 "$ORIGIN_REPO" "$(jq -r '.preview | .body += " edited" | @base64' <<<"$PREVIEW")"
refuses "edited destination in consent" "" run_main 6 "$ORIGIN_REPO" "$(jq -r '.preview | .branch += "-other" | @base64' <<<"$PREVIEW")"
refuses "existing adopted PR disappeared" "" run_main 6 "$ORIGIN_REPO" "$(jq -er .approved_payload <<<"$retry_preview")"
FIXTURE_BRANCH_EXISTS=1 FIXTURE_REMOTE_OID=other refuses "different commit on recovery branch" "" adopt
FIXTURE_BRANCH_EXISTS=1 FIXTURE_OPEN_PR=1 FIXTURE_PR_BASE=other refuses "existing PR targets another base" "" adopt
FIXTURE_FETCHED_OID=other refuses "head changed during fetch" "fetch" adopt

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
