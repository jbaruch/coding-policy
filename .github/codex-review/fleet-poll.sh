#!/usr/bin/env bash
# Enumerate the open pull requests across the reviewer App's installation that
# still need a policy review, and emit them as a JSON worklist for the central
# fleet reviewer. Stateless — no state file: a PR needs review unless the App has
# already posted a review whose commit_id equals the PR's current head SHA (a new
# push moves the head SHA, so it re-enters the worklist).
#
# Env:
#   GH_TOKEN        App installation token, scoped to every installed repo
#   REVIEWER_LOGIN  the App's bot login, e.g. "coding-policy-fleet-reviewer[bot]"
# Out: JSON array on stdout, one entry per PR needing review:
#   [{"owner":..,"repo":..,"number":N,"base_ref":..,"head_sha":..}, ...]
# Exit: 0 on success (array may be empty); non-zero with a stderr diagnostic.
set -euo pipefail

command -v jq >/dev/null 2>&1 || { echo "error: jq is not installed" >&2; exit 2; }

main() {
  : "${REVIEWER_LOGIN:?set REVIEWER_LOGIN to the reviewer App bot login, e.g. coding-policy-fleet-reviewer[bot]}"

  local repos
  repos=$(gh api --paginate /installation/repositories --jq '.repositories[].full_name') \
    || { echo "error: could not list installation repositories — check GH_TOKEN (App installation token)" >&2; exit 1; }

  local worklist="[]"
  local full owner repo prs_json pr number base head headrepo reviews_json reviewed
  while IFS= read -r full; do
    [[ -n "$full" ]] || continue
    owner=${full%%/*}; repo=${full#*/}

    if ! prs_json=$(gh api --paginate "/repos/${full}/pulls?state=open&per_page=100"); then
      echo "warn: could not list open PRs for ${full} — skipping this repo" >&2
      continue
    fi

    # Guard the PR-list parse explicitly: a jq shape/parse failure must skip the
    # repo loudly, never yield a silent empty worklist (rules/error-handling.md).
    local pr_lines
    if ! pr_lines=$(jq -c '.[] | {number, base: .base.ref, head: .head.sha, headrepo: .head.repo.full_name}' <<<"$prs_json"); then
      echo "warn: could not parse the open-PR list for ${full} — skipping this repo" >&2
      continue
    fi

    while IFS= read -r pr; do
      [[ -n "$pr" ]] || continue
      number=$(jq -r '.number'  <<<"$pr")
      base=$(jq -r   '.base'    <<<"$pr")
      head=$(jq -r   '.head'    <<<"$pr")
      headrepo=$(jq -r '.headrepo // ""' <<<"$pr")

      # Skip fork PRs — the App token cannot fetch a head from an un-installed
      # fork; those are adopted via the adopt-fork-pr flow, same as before.
      [[ "$headrepo" == "$full" ]] || continue

      if ! reviews_json=$(gh api --paginate "/repos/${full}/pulls/${number}/reviews"); then
        echo "warn: could not read reviews for ${full}#${number} — treating as needs-review" >&2
        reviews_json="[]"
      fi
      reviewed=$(jq -r --arg who "$REVIEWER_LOGIN" --arg sha "$head" \
                   'any(.[]; .user.login==$who and .commit_id==$sha)' <<<"$reviews_json")
      [[ "$reviewed" == "true" ]] && continue

      worklist=$(jq -c \
        --arg o "$owner" --arg r "$repo" --argjson n "$number" --arg b "$base" --arg h "$head" \
        '. + [{owner:$o, repo:$r, number:$n, base_ref:$b, head_sha:$h}]' <<<"$worklist")
    done <<<"$pr_lines"
  done <<<"$repos"

  printf '%s\n' "$worklist"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
