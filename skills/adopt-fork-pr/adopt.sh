#!/usr/bin/env bash
#
# adopt.sh — Adopt a fork pull request's branch into the base repo so the
# gh-aw policy reviewer (fork-guarded) can run on it.
#
# Usage:   adopt.sh <pr-number>
# Repo:    operates on the current git repository (origin).
#
# What it does (all deterministic):
#   1. Reads PR metadata via `gh pr view`.
#   2. Refuses anything that is not an OPEN cross-repo (fork) PR.
#   3. Computes the adopted branch name:
#        adopt/pr-<N>-<slug(headRefName)>
#      slug = headRefName lowercased, every run of non-[a-z0-9] folded to a
#      single '-', leading/trailing '-' trimmed, truncated to 50 chars.
#   4. Idempotency / partial-run recovery, keyed on the adopted branch:
#        - branch on origin AND an open PR for it  -> "already-adopted" no-op
#        - branch on origin but NO open PR (a prior run pushed then died before
#          `gh pr create`) -> recover: open the PR + comment, emit "adopted"
#        - branch not on origin -> fresh adoption (checkout, push, PR, comment)
#   5. Fresh adoption `gh pr checkout`s the fork head and pushes it to origin
#      under the adopted branch name (commits unchanged, so authorship + any
#      `Co-authored-by:` Author-Model trailer survive), then opens a same-repo
#      PR and comments on the original fork PR. The original is left OPEN.
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
# New-PR body template (verbatim):
#   Adopted from #<N> by @<author> (fork <owner>/<repo>).
#
#   Carries the contributor's original commits unchanged — authorship and any
#   Author-Model trailer are preserved. As a same-repo PR, it gets the policy
#   review.
#
#   Original PR: <url>
#
# Original-PR comment template (verbatim):
#   Adopted into the base repo as <new_pr_url> so the policy reviewer can run —
#   fork PRs are skipped by the reviewer's fork-guard. Leaving this PR open;
#   close it whenever you like, it's your call.
#
# Exit codes:
#   0  success (adopted, recovered, or already-adopted no-op)
#   1  operational failure (dirty tree, push/gh failure, PR not OPEN)
#   2  usage / invalid argument
#   3  PR is not a fork PR (adoption does not apply)
#
set -euo pipefail

emit_jq_missing() {
  printf '{"state":"error","reason":"jq is required but not installed — install it (macOS: brew install jq; Debian/Ubuntu: apt install jq) and re-run."}\n'
}

die() { printf 'adopt.sh: %s\n' "$1" >&2; exit "${2:-1}"; }

slugify() {
  # lowercase → fold non-alnum runs to '-' → trim → cap at 50 chars
  local s
  s=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')
  s=$(printf '%s' "$s" | sed -E 's/-+/-/g; s/^-//; s/-$//')
  printf '%s' "${s:0:50}"
}

open_pr_url_for() {
  # echoes the URL of the open PR whose head is $1, or empty
  gh pr list --head "$1" --state open --json url --jq '.[0].url // empty' 2>/dev/null || true
}

create_adopted_pr() {
  # args: base branch title pr_n author fork_owner fork_repo orig_url
  # opens the same-repo PR, comments on the original, echoes the new PR URL
  local base="$1" branch="$2" title="$3" pr_n="$4" author="$5" fork_owner="$6" fork_repo="$7" orig_url="$8"
  local body comment new_url
  body=$(printf 'Adopted from #%s by @%s (fork %s/%s).\n\nCarries the contributor'\''s original commits unchanged — authorship and any Author-Model trailer are preserved. As a same-repo PR, it gets the policy review.\n\nOriginal PR: %s\n' \
    "$pr_n" "$author" "$fork_owner" "$fork_repo" "$orig_url")
  new_url=$(gh pr create --base "$base" --head "$branch" --title "$title" --body "$body" 2>/dev/null) \
    || die "gh pr create for branch $branch failed." 1
  comment=$(printf 'Adopted into the base repo as %s so the policy reviewer can run — fork PRs are skipped by the reviewer'\''s fork-guard. Leaving this PR open; close it whenever you like, it'\''s your call.\n' "$new_url")
  gh pr comment "$pr_n" --body "$comment" >/dev/null 2>&1 \
    || printf 'adopt.sh: warning — adopted PR created (%s) but commenting on original #%s failed.\n' "$new_url" "$pr_n" >&2
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
  meta=$(gh pr view "$n" --json number,isCrossRepository,headRefName,headRepositoryOwner,headRepository,author,title,url,state,baseRefName 2>/dev/null) \
    || die "could not read PR #$n — check the number and that 'gh auth status' is healthy." 1

  local is_fork state head_ref base_ref fork_owner fork_repo author title url
  is_fork=$(jq -r '.isCrossRepository' <<<"$meta")
  state=$(jq -r '.state' <<<"$meta")
  head_ref=$(jq -r '.headRefName' <<<"$meta")
  base_ref=$(jq -r '.baseRefName' <<<"$meta")
  fork_owner=$(jq -r '.headRepositoryOwner.login // empty' <<<"$meta")
  fork_repo=$(jq -r '.headRepository.name // empty' <<<"$meta")
  author=$(jq -r '.author.login // empty' <<<"$meta")
  title=$(jq -r '.title' <<<"$meta")
  url=$(jq -r '.url' <<<"$meta")

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
    existing=$(open_pr_url_for "$branch")
    if [ -n "$existing" ]; then
      emit "already-adopted" "$branch" "$existing" "$n" "$author"
      exit 0
    fi
    # Branch was pushed but no PR exists — recover by opening it now.
    new_url=$(create_adopted_pr "$base_ref" "$branch" "$title" "$n" "$author" "$fork_owner" "$fork_repo" "$url")
    emit "adopted" "$branch" "$new_url" "$n" "$author"
    exit 0
  fi

  # Fresh adoption: needs a clean tree because gh pr checkout mutates it.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree has uncommitted changes — commit or stash before adopting (gh pr checkout needs a clean tree)." 1
  fi
  local orig_ref
  orig_ref=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || git rev-parse HEAD)

  gh pr checkout "$n" >/dev/null 2>&1 \
    || die "gh pr checkout #$n failed — the fork branch may be unavailable or your tree is not clean." 1
  git push origin "HEAD:refs/heads/$branch" >/dev/null 2>&1 \
    || die "push to origin/$branch failed — you need write access to the base repo." 1
  git checkout --quiet "$orig_ref" 2>/dev/null || true

  new_url=$(create_adopted_pr "$base_ref" "$branch" "$title" "$n" "$author" "$fork_owner" "$fork_repo" "$url")
  emit "adopted" "$branch" "$new_url" "$n" "$author"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
