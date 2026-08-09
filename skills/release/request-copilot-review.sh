#!/usr/bin/env bash
# Request a Copilot review on a PR via GraphQL — REST silently drops bot
# reviewers. Falls back to looking up the bot ID from recent reviews if the
# pinned ID is stale, then verifies Copilot is in the mutation's OWN returned
# review requests. The REST `requested_reviewers` field omits bot reviewers, so
# it cannot verify a bot request (issue #276) — the mutation response is the
# authoritative surface.
#
# Usage: request-copilot-review.sh <owner> <repo> <pr-number>
# Env:   COPILOT_BOT_ID (override default BOT_kgDOCnlnWA)
# Out:   one JSON object on stdout: {"pr_number","bot_id","requested_reviewers"}
# Exit:  0 on verified request; non-zero with stderr diagnostic on failure

set -euo pipefail

COPILOT_BOT_ID_DEFAULT="BOT_kgDOCnlnWA"

fetch_pr_node_id() {
  local owner="$1" repo="$2" pr_number="$3"
  # Validate pr_number is numeric BEFORE building the query: a
  # non-numeric value would either break the GraphQL `Int!` argument
  # or, in a more pathological case, get interpreted as additional
  # query syntax. Refuse early with a clear diagnostic.
  if [[ ! "$pr_number" =~ ^[0-9]+$ ]]; then
    echo "error: pr-number must be a positive integer; got '${pr_number}'" >&2
    return 1
  fi
  local pr_id
  # `// empty` collapses null to nothing, so a missing/invalid PR
  # produces an empty string rather than the literal "null" that --jq
  # would otherwise emit. Without this guard the downstream mutation
  # runs with `pullRequestId: "null"` and surfaces as a confusing
  # GraphQL error several steps removed from the actual root cause.
  pr_id=$(gh api graphql -f query="
    query { repository(owner: \"${owner}\", name: \"${repo}\") {
      pullRequest(number: ${pr_number}) { id }
    } }
  " --jq '.data.repository.pullRequest.id // empty')
  if [[ -z "$pr_id" ]]; then
    # Empty pr_id can come from any of: missing repository, missing
    # PR within an existing repository, insufficient permissions, or
    # a GraphQL error that still returned HTTP 200 with a partial
    # body. The diagnostic stays generic so the operator knows to
    # check all four; pinpointing the exact cause would require
    # parsing the GraphQL `errors` array, which is out of scope here.
    echo "error: failed to resolve PR node ID for PR #${pr_number} in ${owner}/${repo} (repository, permissions, GraphQL, or PR lookup may have failed)" >&2
    return 1
  fi
  echo "$pr_id"
}

# Run the requestReviews mutation and echo the resulting bot-reviewer logins as
# a JSON array. The mutation's OWN response is the authoritative post-state and
# the only surface that reports bot reviewers — the REST pulls endpoint's
# `requested_reviewers` omits them (issue #276), so it cannot verify this. On a
# GraphQL error (a stale/rejected bot ID) `gh api graphql` exits non-zero and
# stdout is empty, so a caller can branch on the exit to fall back. stderr is
# silenced because every caller emits its own actionable message on failure
# (rules/error-handling.md — silencing a diagnostic while explicitly handling
# the failure is not suppression).
request_with_bot_id() {
  gh api graphql -f query="
    mutation { requestReviews(input: {
      pullRequestId: \"$1\", botIds: [\"$2\"]
    }) { pullRequest { reviewRequests(first: 20) { nodes {
      requestedReviewer { __typename ... on Bot { login } }
    } } } } }
  " --jq '[.data.requestReviews.pullRequest.reviewRequests.nodes[]?.requestedReviewer.login // empty]' 2>/dev/null
}

discover_copilot_bot_id() {
  # The Bot type's `login` is reported with the `[bot]` suffix in some
  # GraphQL contexts and without it in others (the REST surface keeps
  # the suffix; GraphQL is inconsistent). Match either form so the
  # filter does not silently miss a real Copilot review and run the
  # mutation against an empty/wrong actor ID.
  gh api graphql -f query="
    query { repository(owner: \"$1\", name: \"$2\") {
      pullRequests(last: 20) { nodes { reviews(first: 10) {
        nodes { author { ... on Bot { id login } } }
      } } }
    } }
  " --jq '[.data.repository.pullRequests.nodes[].reviews.nodes[]
           | select(.author.login == "copilot-pull-request-reviewer"
                    or .author.login == "copilot-pull-request-reviewer[bot]")
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
  local reviewers
  # The mutation returns the post-request reviewer list; capture it as both the
  # request AND the verification (issue #276). A non-zero exit means the pinned
  # ID was rejected — discover the live one from review history and retry.
  if ! reviewers=$(request_with_bot_id "$pr_node_id" "$bot_id"); then
    echo "warn: pinned bot ID $bot_id rejected; discovering from review history" >&2
    bot_id=$(discover_copilot_bot_id "$owner" "$repo") || {
      echo "error: failed to query review history" >&2; exit 1;
    }
    if [[ -z "$bot_id" ]]; then
      echo "error: no Copilot bot ID found in recent reviews of ${owner}/${repo}" >&2
      exit 1
    fi
    reviewers=$(request_with_bot_id "$pr_node_id" "$bot_id") || {
      echo "error: request failed with discovered bot ID $bot_id" >&2; exit 1;
    }
  fi

  if ! echo "$reviewers" | jq -e 'any(test("copilot"; "i"))' >/dev/null 2>&1; then
    echo "error: Copilot not in review requests after request: $reviewers" >&2
    exit 1
  fi

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg bot_id "$bot_id" \
    --argjson reviewers "$reviewers" \
    '{pr_number: $pr_number, bot_id: $bot_id, requested_reviewers: $reviewers}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
