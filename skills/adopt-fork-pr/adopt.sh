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
#   4. Idempotency: if the adopted branch already exists on origin, re-emits the
#      existing state instead of pushing or opening a duplicate PR.
#   5. `gh pr checkout`s the fork head, pushes it to origin under the adopted
#      branch name (commits unchanged, so authorship + any `Co-authored-by:`
#      Author-Model trailer survive), opens a same-repo PR, and comments on the
#      original fork PR linking the adopted one. The original is left OPEN.
#
# Output: a single JSON object on stdout:
#   {"state": "...", "adopted_branch": "...", "new_pr_url": "...",
#    "original_pr": N, "author": "..."}
#   state ∈ {"adopted", "already-adopted"}
#
# New-PR body template (verbatim):
#   Adopted from #<N> by @<author> (fork <owner>/<repo>).
#
#   This branch carries the contributor's original commits unchanged, so
#   authorship and the Author-Model declaration are preserved. The policy
#   reviewer runs here because this is a same-repo PR.
#
#   Original PR: <url>
#
# Original-PR comment template (verbatim):
#   Adopted into the base repo as <new_pr_url> so the policy reviewer can run —
#   fork PRs are skipped by the reviewer's fork-guard. Leaving this PR open;
#   close it whenever you like, it's your call.
#
# Exit codes:
#   0  success (adopted, or already-adopted no-op)
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

main() {
  command -v jq >/dev/null 2>&1 || { emit_jq_missing; exit 1; }
  command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) not found — install it and run 'gh auth login'." 1
  command -v git >/dev/null 2>&1 || die "git not found." 1

  [ "$#" -eq 1 ] || die "usage: adopt.sh <pr-number>" 2
  local n="$1"
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || die "PR number must be a positive integer, got: $n" 2

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not inside a git work tree." 1
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree has uncommitted changes — commit or stash before adopting (gh pr checkout needs a clean tree)." 1
  fi

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

  local branch
  branch="adopt/pr-${n}-$(slugify "$head_ref")"

  # Idempotency: adopted branch already on origin → re-emit existing state.
  if [ -n "$(git ls-remote --heads origin "refs/heads/$branch" 2>/dev/null)" ]; then
    local existing
    existing=$(gh pr list --head "$branch" --state open --json url --jq '.[0].url // empty' 2>/dev/null || true)
    jq -nc --arg b "$branch" --arg u "$existing" --argjson n "$n" --arg a "$author" \
      '{state:"already-adopted",adopted_branch:$b,new_pr_url:$u,original_pr:$n,author:$a}'
    exit 0
  fi

  local orig_ref
  orig_ref=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || git rev-parse HEAD)

  gh pr checkout "$n" >/dev/null 2>&1 \
    || die "gh pr checkout #$n failed — the fork branch may be unavailable or your tree is not clean." 1
  git push origin "HEAD:refs/heads/$branch" >/dev/null 2>&1 \
    || die "push to origin/$branch failed — you need write access to the base repo." 1

  # Restore the caller's original branch before opening the PR.
  git checkout --quiet "$orig_ref" 2>/dev/null || true

  local body comment new_url
  body=$(printf 'Adopted from #%s by @%s (fork %s/%s).\n\nThis branch carries the contributor'\''s original commits unchanged, so authorship and the Author-Model declaration are preserved. The policy reviewer runs here because this is a same-repo PR.\n\nOriginal PR: %s\n' \
    "$n" "$author" "$fork_owner" "$fork_repo" "$url")

  new_url=$(gh pr create --base "$base_ref" --head "$branch" --title "$title" --body "$body" 2>/dev/null) \
    || die "gh pr create for branch $branch failed." 1

  comment=$(printf 'Adopted into the base repo as %s so the policy reviewer can run — fork PRs are skipped by the reviewer'\''s fork-guard. Leaving this PR open; close it whenever you like, it'\''s your call.\n' "$new_url")
  gh pr comment "$n" --body "$comment" >/dev/null 2>&1 \
    || printf 'adopt.sh: warning — adopted PR created (%s) but commenting on original #%s failed.\n' "$new_url" "$n" >&2

  jq -nc --arg b "$branch" --arg u "$new_url" --argjson n "$n" --arg a "$author" \
    '{state:"adopted",adopted_branch:$b,new_pr_url:$u,original_pr:$n,author:$a}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
