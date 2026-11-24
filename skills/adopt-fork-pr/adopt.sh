#!/usr/bin/env bash
#
# adopt.sh — Adopt a fork pull request's branch into the base repo so the
# policy reviewer (fork-guarded) can run on it.
#
# Usage: adopt.sh metadata <pr-number>
#        adopt.sh preview <pr-number> <repo> <metadata_payload>
#        adopt.sh <pr-number> <repo> <approved_payload>
# Metadata and preview are read-only. All calls bind origin's fetch and push URLs using
# skills/release/origin-repo.py. Preview renders the fork PR metadata, the
# destination adopt/pr-<N>-<slug(headRefName)> and both outgoing messages.
# Execution reads the same fields again and requires the approved action.
# A fresh adoption fetches the PR ref and pushes the approved commit to a
# previously absent branch. A matching branch can resume at PR creation.
# An existing same-repo PR for that branch and base can resume at the
# pointer comment. A pointer already present makes the retry a no-op.
#
# Metadata outputs {repo, number, isCrossRepository, metadata_payload}.
# Preview outputs {preview, approved_payload}. Each payload is Base64 of
# the corresponding JSON bytes including the producer's final newline.
# Execution outputs a single JSON object on stdout:
#   {"state": "...", "adopted_branch": "...", "new_pr_url": "...",
#    "original_pr": N, "author": "..."}
#   state ∈ {"adopted", "already-adopted"}
#   Error path: when jq itself is missing, the script emits
#   {"state": "error", "reason": "..."} on stdout (so stdout-parsing wrappers
#   still get a payload) AND a diagnostic on stderr, then exits 1 — jq is
#   required to format the normal envelope, so the failure is hand-rolled. Every
#   other failure is a stderr diagnostic with the exit code below (no stdout JSON).
#
# Exit codes:
#   0  success (adopted, recovered, or already-adopted no-op)
#   1  operational failure or unavailable/changed preview inputs
#   2  usage / invalid argument
#   3  PR is not a fork PR (adoption does not apply)
#
set -euo pipefail

repo=""       # origin's GitHub repository as <owner>/<repo>; every gh call names it

# This script's directory, resolved when it is run or sourced. A sentinel
# carries it across command substitution's trailing-newline strip.
_adopt_src="${BASH_SOURCE[0]}"
case "$_adopt_src" in
  */*) _adopt_dir="${_adopt_src%/*}" ;;
  *) _adopt_dir=. ;;
esac
if ! ADOPT_DIR="$(CDPATH='' cd -- "${_adopt_dir:-/}" && pwd && printf x)"; then
  printf 'adopt.sh: cannot enter the script directory %s — reinstall the plugin\n' "${_adopt_dir:-/}" >&2
  exit 1
fi
ADOPT_DIR="${ADOPT_DIR%x}"
ADOPT_DIR="${ADOPT_DIR%$'\n'}"

emit_jq_missing() {
  # jq is needed to format the normal envelope, so hand-roll the failure: a JSON
  # payload on stdout for stdout-parsing wrappers AND a diagnostic on stderr for
  # stderr-only watchers.
  local msg="jq is required but not installed — install it (macOS: brew install jq; Debian/Ubuntu: apt install jq) and re-run."
  printf '{"state":"error","reason":"%s"}\n' "$msg"
  printf 'adopt.sh: %s\n' "$msg" >&2
}

die() { printf 'adopt.sh: %s\n' "$1" >&2; exit "${2:-1}"; }

fresh_preview() { die "$1 — get a fresh preview before adopting." "${2:-1}"; }

slugify() {
  # lowercase → fold non-alnum runs to '-' → trim → cap at 50 chars
  local s
  s=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')
  s=$(printf '%s' "$s" | sed -E 's/-+/-/g; s/^-//; s/-$//')
  printf '%s' "${s:0:50}"
}

ensure_pointer_comment() {
  # args: original_pr adopted_url approved_comment
  # Idempotent: skip when the original already links the adopted URL. Both the
  # read probe and the post are operational errors on failure, not warnings —
  # capture the read first and die rather than letting a failed probe look like
  # "no comment" (which would duplicate the pointer on rerun).
  local original_pr="$1" adopted_url="$2" comment="$3" existing_comments
  existing_comments=$(gh pr view "$original_pr" --repo "github.com/$repo" --json comments) \
    || die "could not read comments on original #$original_pr to check for an existing pointer — see the gh error above." 1
  if grep -qF "$adopted_url" <<<"$existing_comments"; then
    return 0
  fi
  comment=${comment/<new_pr_url>/"$adopted_url"}
  gh pr comment "$original_pr" --repo "github.com/$repo" --body "$comment" >/dev/null \
    || die "adopted PR ($adopted_url) exists but posting the pointer comment on original #$original_pr failed — see the gh error above; rerun to retry." 1
}

emit() {
  # args: state adopted_branch new_pr_url pr_n author
  jq -nc --arg s "$1" --arg b "$2" --arg u "$3" --argjson n "$4" --arg a "$5" \
    '{state:$s,adopted_branch:$b,new_pr_url:$u,original_pr:$n,author:$a}'
}

resolve_repo() {
  # Sets repo to origin's GitHub repository, or dies with origin-repo.py's
  # own diagnostic above.
  local helper="${ADOPT_DIR}/../release/origin-repo.py" out
  [[ -f "$helper" && -r "$helper" ]] \
    || die "${helper} is not readable, so origin cannot be matched to a GitHub repository — reinstall the plugin." 1
  out=$(python3 "$helper" .) \
    || fresh_preview "cannot resolve origin — see the origin-repo error above"
  if ! repo=$(jq -r '.repo // empty' <<<"$out"); then
    die "cannot read origin-repo.py's answer — run 'python3 ${helper} .' to inspect it." 1
  fi
  if [ -z "$repo" ]; then
    die "origin-repo.py answered without a repository — run 'python3 ${helper} .' to inspect it." 1
  fi
}

read_metadata() {
  gh pr view "$1" --repo "github.com/$repo" --json number,isCrossRepository,headRefName,headRefOid,headRepositoryOwner,headRepository,author,title,url,state,baseRefName \
    | python3 "$ADOPT_DIR/validate-handoff.py"
}

main() {
  command -v jq >/dev/null 2>&1 || { emit_jq_missing; exit 1; }
  command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) not found — install it and run 'gh auth login'." 1
  command -v git >/dev/null 2>&1 || die "git not found." 1
  command -v python3 >/dev/null 2>&1 || die "python3 not found — install it and re-run." 1

  local mode=adopt
  case ${1:-} in
    metadata|preview) mode=$1; shift ;;
  esac
  if [[ $mode == metadata ]]; then
    [[ $# == 1 ]] || die "usage: adopt.sh metadata <pr-number>" 2
  else
    [[ $# == 3 ]] || fresh_preview "usage: adopt.sh [preview] <pr-number> <repo> <payload>" 2
  fi
  local n="$1"
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || fresh_preview "PR number must be a positive integer" 2

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fresh_preview "not inside a git work tree"
  resolve_repo
  local meta input
  if [[ $mode == metadata ]]; then
    meta=$(read_metadata "$n" | jq -Rsec --arg repo "$repo" --argjson n "$n" '
      . as $raw | fromjson |
      select(.number == $n and (.isCrossRepository | type == "boolean")) |
      {repo: $repo, number, isCrossRepository, metadata_payload: ($raw | @base64)}
    ') || die "could not classify PR #$n — check the gh diagnostic and PR number."
    printf '%s\n' "$meta"
    return
  fi
  [[ "$repo" == "$2" ]] || fresh_preview "origin differs from the preview repository"
  # Keep trailing JSON whitespace through command substitution.
  input=$(python3 "$ADOPT_DIR/validate-handoff.py" "$3" && printf x) \
    || fresh_preview "invalid handoff payload"
  input=${input%x}
  if [[ $mode == preview ]]; then
    meta=$input
  else
    meta=$(read_metadata "$n") \
      || fresh_preview "could not read PR #$n"
  fi
  if jq -e '.isCrossRepository == false' <<<"$meta" >/dev/null; then
    fresh_preview "PR #$n is a same-repo PR — the reviewer already covers it" 3
  fi
  local head_ref head_oid base_ref branch author
  if ! jq -e --argjson n "$n" '
    .number == $n and .isCrossRepository == true and .state == "OPEN" and
    all(.headRefName, .headRefOid, .baseRefName, .headRepositoryOwner.login,
        .headRepository.name, .author.login, .title, .url;
        type == "string" and length > 0) and
    (.headRefOid | test("^[0-9a-f]{40}$"))
  ' <<<"$meta" >/dev/null; then
    fresh_preview "open fork metadata for #$n is incomplete or unavailable"
  fi
  head_ref=$(jq -r '.headRefName' <<<"$meta")
  head_oid=$(jq -r '.headRefOid' <<<"$meta")
  base_ref=$(jq -r '.baseRefName' <<<"$meta")
  branch="adopt/pr-${n}-$(slugify "$head_ref")"

  local remote_heads existing="" prs
  remote_heads=$(git ls-remote --heads origin "refs/heads/$branch") \
    || fresh_preview "git ls-remote origin failed — check network/auth to the base repo"
  if [[ -n "$remote_heads" ]]; then
    [[ "$remote_heads" == "$head_oid"$'\t'"refs/heads/$branch" ]] \
      || fresh_preview "origin/$branch differs from the fork head"
    prs=$(gh pr list --repo "github.com/$repo" --head "$branch" --state open --json url,isCrossRepository,headRefName,headRefOid,baseRefName) \
      || fresh_preview "could not look up the adopted PR"
    existing=$(jq -er --arg branch "$branch" --arg base "$base_ref" --arg oid "$head_oid" '
      if . == [] then "" else
        select(length == 1) | .[0] |
        select(.isCrossRepository == false and .headRefName == $branch and
               .headRefOid == $oid and .baseRefName == $base) | .url | select(type == "string" and length > 0)
      end' <<<"$prs") || fresh_preview "existing PR does not match the adoption destination"
  fi

  local action pointer_template pointer credit
  pointer_template="Adopted into the base repo as <new_pr_url> so the policy reviewer can run — fork PRs are skipped by the reviewer's fork-guard. Leaving this PR open; close it whenever you like, it's your call."
  pointer=${pointer_template/<new_pr_url>/"${existing:-<new_pr_url>}"}
  credit="Carries the contributor's original commits unchanged — authorship is preserved. As a same-repo PR, it gets the policy review."
  action=$(jq -c --arg repo "$repo" --arg branch "$branch" --arg pointer "$pointer" --arg credit "$credit" '
    (.headRepositoryOwner.login + "/" + .headRepository.name) as $fork |
    {repo: $repo, number, fork: $fork, headRefName, headRefOid, baseRefName, branch: $branch,
     author: .author.login, title, pointer: $pointer,
     body: "Adopted from #\(.number) by @\(.author.login) (fork \($fork)).\n\n\($credit)\n\nOriginal PR: \(.url)"}
  ' <<<"$meta")
  if [[ $mode == preview ]]; then
    printf '%s\n' "$action" | jq -Rsc '{preview: fromjson, approved_payload: @base64}'
    return
  fi
  # An unresolved link covers the URL created by an earlier partial attempt.
  # A link already shown in the question must still identify the same PR.
  if ! jq -en --argjson approved "$input" --argjson current "$action" --arg unresolved "$pointer_template" '
    ($approved | if .pointer == $unresolved then .pointer = $current.pointer else . end) == $current
  ' >/dev/null; then
    fresh_preview "the approved action is unavailable or has changed"
  fi

  local title body new_url state=adopted fetched
  # Preserve trailing newlines in both consented text arguments.
  branch=$(jq -r '.branch' <<<"$input")
  head_oid=$(jq -r '.headRefOid' <<<"$input")
  base_ref=$(jq -r '.baseRefName' <<<"$input")
  title=$(jq -r '.title + "x"' <<<"$input")
  body=$(jq -r '.body + "x"' <<<"$input")
  pointer=$(jq -r '.pointer' <<<"$input")
  author=$(jq -r '.author' <<<"$input")

  if [[ -z "$remote_heads" ]]; then
    git fetch --no-tags origin "refs/pull/$n/head" >&2 || fresh_preview "could not fetch PR #$n"
    fetched=$(git rev-parse --verify FETCH_HEAD) || fresh_preview "could not identify the fetched commit"
    [[ "$fetched" == "$head_oid" ]] || fresh_preview "the fetched head differs from the approved commit"
    git push --no-follow-tags --force-with-lease="refs/heads/$branch:" origin "$head_oid:refs/heads/$branch" >&2 \
      || fresh_preview "could not create origin/$branch — check write access and whether the branch now exists"
  fi
  if [[ -n "$existing" ]]; then
    new_url=$existing
    state=already-adopted
  else
    new_url=$(gh pr create --repo "github.com/$repo" --base "$base_ref" --head "$branch" --title "${title%x}" --body "${body%x}") \
      || die "gh pr create for $branch failed — check write access and retry."
  fi
  [[ -n "$new_url" ]] || die "missing adopted PR URL — check the PR and retry."
  ensure_pointer_comment "$n" "$new_url" "$pointer"
  emit "$state" "$branch" "$new_url" "$n" "$author"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
