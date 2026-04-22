#!/usr/bin/env bash
# Request a Copilot review on a PR via GraphQL — REST silently drops bot
# reviewers. Falls back to looking up the bot ID from recent reviews if the
# pinned ID is stale, then verifies Copilot is in `requested_reviewers`.
#
# Usage: request-copilot-review.sh <owner> <repo> <pr-number>
# Env:   COPILOT_BOT_ID (override default BOT_kgDOCnlnWA)
# Out:   one JSON object on stdout: {"pr_number","bot_id","requested_reviewers"}
# Exit:  0 on verified request; non-zero with stderr diagnostic on failure

set -euo pipefail

COPILOT_BOT_ID_DEFAULT="BOT_kgDOCnlnWA"

fetch_pr_node_id() {
  gh api graphql -f query="
    query { repository(owner: \"$1\", name: \"$2\") {
      pullRequest(number: $3) { id }
    } }
  " --jq '.data.repository.pullRequest.id'
}

request_with_bot_id() {
  gh api graphql -f query="
    mutation { requestReviews(input: {
      pullRequestId: \"$1\", botIds: [\"$2\"]
    }) { clientMutationId } }
  " >/dev/null 2>&1
}

discover_copilot_bot_id() {
  gh api graphql -f query="
    query { repository(owner: \"$1\", name: \"$2\") {
      pullRequests(last: 20) { nodes { reviews(first: 10) {
        nodes { author { ... on Bot { id login } } }
      } } }
    } }
  " --jq '[.data.repository.pullRequests.nodes[].reviews.nodes[]
           | select(.author.login == "copilot-pull-request-reviewer")
           | .author.id] | unique | .[0] // empty'
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr_number="$3"

  local pr_node_id
  pr_node_id=$(fetch_pr_node_id "$owner" "$repo" "$pr_number") || {
    echo "error: failed to fetch PR node ID for ${owner}/${repo}#${pr_number}" >&2
    exit 1
  }

  local bot_id="${COPILOT_BOT_ID:-$COPILOT_BOT_ID_DEFAULT}"
  if ! request_with_bot_id "$pr_node_id" "$bot_id"; then
    echo "warn: pinned bot ID $bot_id rejected; discovering from review history" >&2
    bot_id=$(discover_copilot_bot_id "$owner" "$repo") || {
      echo "error: failed to query review history" >&2; exit 1;
    }
    if [[ -z "$bot_id" ]]; then
      echo "error: no Copilot bot ID found in recent reviews of ${owner}/${repo}" >&2
      exit 1
    fi
    request_with_bot_id "$pr_node_id" "$bot_id" || {
      echo "error: request failed with discovered bot ID $bot_id" >&2; exit 1;
    }
  fi

  local reviewers
  reviewers=$(gh api "repos/${owner}/${repo}/pulls/${pr_number}" \
    --jq '[.requested_reviewers[]?.login // empty]') || {
    echo "error: verification query failed" >&2; exit 1;
  }
  if ! echo "$reviewers" | jq -e 'any(test("copilot"; "i"))' >/dev/null 2>&1; then
    echo "error: Copilot not in requested_reviewers: $reviewers" >&2
    exit 1
  fi

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg bot_id "$bot_id" \
    --argjson reviewers "$reviewers" \
    '{pr_number: $pr_number, bot_id: $bot_id, requested_reviewers: $reviewers}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
