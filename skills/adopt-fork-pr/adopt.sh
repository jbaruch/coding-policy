#!/usr/bin/env bash
#
# adopt.sh — Adopt a fork pull request's branch into the base repo so the
# gh-aw policy reviewer (fork-guarded) can run on it.
#
# Usage:   adopt.sh <pr-number>
# Repo:    operates on the current git repository (origin).
#
# What it does (all deterministic):
#   1. Reads PR metadata via `gh pr view` (including the PR body).
#   2. Refuses anything that is not an OPEN cross-repo (fork) PR.
#   3. Computes the adopted branch name:
#        adopt/pr-<N>-<slug(headRefName)>
#      slug = headRefName lowercased, every run of non-[a-z0-9] folded to a
#      single '-', leading/trailing '-' trimmed, truncated to 50 chars.
#   4. Idempotency / partial-run recovery, keyed on the adopted branch:
#        - branch on origin AND an open PR for it  -> "already-adopted" (still
#          ensures the original carries the pointer comment, repairing a prior
#          run that died before commenting)
#        - branch on origin but NO open PR (a prior run pushed then died before
#          `gh pr create`) -> recover: open the PR + comment, emit "adopted"
#        - branch not on origin -> fresh adoption (checkout, push, PR, comment)
#   5. Fresh adoption `gh pr checkout`s the fork head and pushes it to origin
#      under the adopted branch name (commits unchanged, so authorship + any
#      `Co-authored-by:` Author-Model trailer survive), then opens a same-repo
#      PR and comments on the original fork PR. The original is left OPEN. An
#      EXIT trap restores the caller's original branch even if a later step
#      fails after `gh pr checkout`.
#   6. The pointer comment on the original PR is part of the contract: it is
#      idempotent (skipped when the original already links the adopted URL) and
#      a posting failure is an operational error (exit 1), not a warning.
#   7. Author-Model continuity: if the original PR body carried an
#      `**Author-Model:**` line (a body-only declaration the preserved commits
#      would NOT reproduce), that exact line is prepended to the adopted PR
#      body so the reviewer's declaration gate passes immediately.
#
# Output: a single JSON object on stdout:
#   {"state": "...", "adopted_branch": "...", "new_pr_url": "...",
#    "original_pr": N, "author": "..."}
#   state ∈ {"adopted", "already-adopted"}
#   Error path: when jq itself is missing, stdout instead carries
#   {"state": "error", "reason": "..."} and the script exits 1 — jq is required
#   to format the normal envelope, so the failure is hand-rolled. Every other
#   failure is a stderr diagnostic with the exit code below (no stdout JSON).
#
# New-PR body template (verbatim; the Author-Model line is present only when the
# original PR body carried one):
#   [**Author-Model:** <copied from original body>]
#
#   Adopted from #<N> by @<author> (fork <owner>/<repo>).
#
#   Carries the contributor's original commits unchanged — authorship and any
#   Author-Model trailer are preserved. As a same-repo PR, it gets the policy
#   review.
#
#   Original PR: <url>
#
# Original-PR pointer-comment template (verbatim):
#   Adopted into the base repo as <new_pr_url> so the policy reviewer can run —
#   fork PRs are skipped by the reviewer's fork-guard. Leaving this PR open;
#   close it whenever you like, it's your call.
#
# Exit codes:
#   0  success (adopted, recovered, or already-adopted no-op)
#   1  operational failure (dirty tree, gh/git failure, pointer-comment failure,
#      PR not OPEN)
#   2  usage / invalid argument
#   3  PR is not a fork PR (adoption does not apply)
#
set -euo pipefail

orig_ref=""   # caller's branch; restored by the EXIT trap once fresh adoption starts

emit_jq_missing() {
  printf '{"state":"error","reason":"jq is required but not installed — install it (macOS: brew install jq; Debian/Ubuntu: apt install jq) and re-run."}\n'
}

die() { printf 'adopt.sh: %s\n' "$1" >&2; exit "${2:-1}"; }

restore_orig_ref() {
  if [ -n "$orig_ref" ]; then
    git checkout --quiet "$orig_ref" >/dev/null 2>&1 || true
  fi
}

slugify() {
  # lowercase → fold non-alnum runs to '-' → trim → cap at 50 chars
  local s
  s=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')
  s=$(printf '%s' "$s" | sed -E 's/-+/-/g; s/^-//; s/-$//')
  printf '%s' "${s:0:50}"
}

extract_author_model_line() {
  # echoes the first `**Author-Model:**` / `Author-Model:` line in $1 (trimmed),
  # or empty. grep no-match (exit 1) is swallowed; the line is data, not status.
  printf '%s\n' "$1" \
    | grep -m1 -E '^[[:space:]]*\*{0,2}Author-Model:' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
    || true
}

open_pr_url_for() {
  # echoes the URL of the open PR whose head is $1, or empty if none.
  # Exits non-zero (with gh's stderr intact) if the query itself fails, so the
  # caller can distinguish "no PR" from "couldn't ask".
  gh pr list --head "$1" --state open --json url --jq '.[0].url // empty'
}

ensure_pointer_comment() {
  # args: original_pr adopted_url
  # Idempotent: skip when the original already links the adopted URL. A posting
  # failure is an operational error, not a warning.
  local original_pr="$1" adopted_url="$2" comment
  if gh pr view "$original_pr" --json comments 2>/dev/null | grep -qF "$adopted_url"; then
    return 0
  fi
  comment=$(printf 'Adopted into the base repo as %s so the policy reviewer can run — fork PRs are skipped by the reviewer'\''s fork-guard. Leaving this PR open; close it whenever you like, it'\''s your call.\n' "$adopted_url")
  gh pr comment "$original_pr" --body "$comment" >/dev/null \
    || die "adopted PR ($adopted_url) exists but posting the pointer comment on original #$original_pr failed — see the gh error above; rerun to retry." 1
}

create_adopted_pr() {
  # args: base branch title pr_n author fork_owner fork_repo orig_url am_line
  # opens the same-repo PR, links the original, echoes the new PR URL
  local base="$1" branch="$2" title="$3" pr_n="$4" author="$5" fork_owner="$6" fork_repo="$7" orig_url="$8" am_line="${9:-}"
  local header="" body new_url
  if [ -n "$am_line" ]; then
    header="${am_line}"$'\n\n'
  fi
  body=$(printf '%sAdopted from #%s by @%s (fork %s/%s).\n\nCarries the contributor'\''s original commits unchanged — authorship and any Author-Model trailer are preserved. As a same-repo PR, it gets the policy review.\n\nOriginal PR: %s\n' \
    "$header" "$pr_n" "$author" "$fork_owner" "$fork_repo" "$orig_url")
  new_url=$(gh pr create --base "$base" --head "$branch" --title "$title" --body "$body") \
    || die "gh pr create for branch $branch failed — see the gh error above (permissions, an existing PR for the branch, or validation)." 1
  ensure_pointer_comment "$pr_n" "$new_url"
  printf '%s' "$new_url"
}

emit() {
  # args: state adopted_branch new_pr_url pr_n author
  jq -nc --arg s "$1" --arg b "$2" --arg u "$3" --argjson n "$4" --arg a "$5" \
    '{state:$s,adopted_branch:$b,new_pr_url:$u,original_pr:$n,author:$a}'
}

main() {
  command -v jq >/dev/null 2>&1 || { emit_jq_missing; exit 1; }
  command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) not found — install it and run 'gh auth login'." 1
  command -v git >/dev/null 2>&1 || die "git not found." 1

  [ "$#" -eq 1 ] || die "usage: adopt.sh <pr-number>" 2
  local n="$1"
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || die "PR number must be a positive integer, got: $n" 2

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not inside a git work tree." 1

  local meta
  meta=$(gh pr view "$n" --json number,isCrossRepository,headRefName,headRepositoryOwner,headRepository,author,title,url,state,baseRefName,body) \
    || die "could not read PR #$n — see the gh error above; check the number and that 'gh auth status' is healthy." 1

  local is_fork state head_ref base_ref fork_owner fork_repo author title url orig_body am_line
  is_fork=$(jq -r '.isCrossRepository' <<<"$meta")
  state=$(jq -r '.state' <<<"$meta")
  head_ref=$(jq -r '.headRefName' <<<"$meta")
  base_ref=$(jq -r '.baseRefName' <<<"$meta")
  fork_owner=$(jq -r '.headRepositoryOwner.login // empty' <<<"$meta")
  fork_repo=$(jq -r '.headRepository.name // empty' <<<"$meta")
  author=$(jq -r '.author.login // empty' <<<"$meta")
  title=$(jq -r '.title' <<<"$meta")
  url=$(jq -r '.url' <<<"$meta")
  orig_body=$(jq -r '.body // empty' <<<"$meta")
  am_line=$(extract_author_model_line "$orig_body")

  [ "$is_fork" = "true" ] || die "PR #$n is a same-repo PR — adoption only applies to fork PRs; the reviewer already covers it." 3
  [ "$state" = "OPEN" ] || die "PR #$n is $state — only OPEN fork PRs can be adopted." 1

  local branch new_url
  branch="adopt/pr-${n}-$(slugify "$head_ref")"

  # Branch already on origin: distinguish a complete prior run from a partial one.
  local remote_heads
  remote_heads=$(git ls-remote --heads origin "refs/heads/$branch") \
    || die "git ls-remote origin failed — check network/auth to the base repo." 1
  if [ -n "$remote_heads" ]; then
    local existing
    existing=$(open_pr_url_for "$branch") \
      || die "gh pr list failed while checking for an existing adopted PR — see the gh error above; do not assume none exists." 1
    if [ -n "$existing" ]; then
      ensure_pointer_comment "$n" "$existing"   # repair a missing link from a prior partial run
      emit "already-adopted" "$branch" "$existing" "$n" "$author"
      exit 0
    fi
    # Branch was pushed but no PR exists — recover by opening it now.
    new_url=$(create_adopted_pr "$base_ref" "$branch" "$title" "$n" "$author" "$fork_owner" "$fork_repo" "$url" "$am_line")
    emit "adopted" "$branch" "$new_url" "$n" "$author"
    exit 0
  fi

  # Fresh adoption: needs a clean tree because gh pr checkout mutates it.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree has uncommitted changes — commit or stash before adopting (gh pr checkout needs a clean tree)." 1
  fi
  orig_ref=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || git rev-parse HEAD)
  trap restore_orig_ref EXIT   # restore the caller's branch even if a later step fails

  gh pr checkout "$n" >/dev/null \
    || die "gh pr checkout #$n failed — see the gh error above; the fork branch may be unavailable or your tree is not clean." 1
  git push origin "HEAD:refs/heads/$branch" >/dev/null \
    || die "push to origin/$branch failed — see the git error above; you need write access to the base repo." 1

  new_url=$(create_adopted_pr "$base_ref" "$branch" "$title" "$n" "$author" "$fork_owner" "$fork_repo" "$url" "$am_line")
  emit "adopted" "$branch" "$new_url" "$n" "$author"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
