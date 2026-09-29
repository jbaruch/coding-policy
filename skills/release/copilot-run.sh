#!/usr/bin/env bash
# Shared review-request state helpers. Sourced by poll-pr-reviews.sh and
# request-copilot-review.sh — defines copilot_run_in_flight(),
# fetch_requested_logins() and requested_among() with no other side effects.
# It sets `set -euo pipefail` at the top; both callers already run under it.
# Direct execution is a guarded CLI (entry-point guard at the foot of the
# file): `copilot-run.sh <owner> <repo> <pr-number>`
# prints {"in_flight": bool} and exits 0, or exits non-zero with a stderr
# diagnostic when the timeline read fails.
#
# copilot_run_in_flight <owner> <repo> <pr-number>: prints true when the PR
# timeline's last `copilot_work_started` comes after both the last Copilot
# `review_requested` and the last Copilot `reviewed` event, else false. A
# started run consumes its request, and removing a request while the run is in
# progress discards the run's result (#641). Order is timeline position (the
# API returns events in order), not timestamp: a request and its run start can
# share a second.

set -euo pipefail

copilot_run_in_flight() {
  local owner="$1" repo="$2" pr="$3" timeline
  if ! timeline=$(gh api --paginate "repos/${owner}/${repo}/issues/${pr}/timeline?per_page=100" | jq -s 'add // []'); then
    echo "error: failed to read the timeline of ${owner}/${repo}#${pr} — check 'gh auth status', then retry 'gh api --paginate repos/${owner}/${repo}/issues/${pr}/timeline'" >&2
    return 1
  fi
  printf '%s' "$timeline" | jq '
    [.[] | select(type == "object")] | to_entries as $ev
    | ([$ev[] | select(.value.event == "copilot_work_started") | .key] | max) as $w
    | ([$ev[] | select(.value.event == "review_requested")
              | select((.value.requested_reviewer.login // "") | test("copilot"; "i"))
              | .key] | max) as $r
    | ([$ev[] | select(.value.event == "reviewed")
              | select((.value.user.login // "") | test("copilot"; "i"))
              | .key] | max) as $s
    | $w != null and $w > ($r // -1) and $w > ($s // -1)'
}

# Logins with a review request still pending on the PR, lowercased and with the
# `[bot]` suffix stripped so one spelling compares against another. GraphQL, not
# the REST `requested_reviewers` endpoint: that endpoint omits bot reviewers
# entirely (#276), so every bot lane would read "never requested" there.
fetch_requested_logins() {
  local owner="$1" repo="$2" pr="$3"
  gh api graphql -f query="
    query { repository(owner: \"${owner}\", name: \"${repo}\") {
      pullRequest(number: ${pr}) {
        reviewRequests(first: 50) { nodes { requestedReviewer {
          __typename
          ... on Bot { login }
          ... on User { login }
          ... on Team { slug }
        } } }
      }
    } }
  " --jq '[.data.repository.pullRequest.reviewRequests.nodes[]?.requestedReviewer
           | (.login // .slug) | select(. != null) | ascii_downcase | sub("\\[bot\\]$"; "")]' \
    | jq -c '.'
}

# Is any of <login...> among the pending review requests?
requested_among() { # <requested-json> <login...>
  local requested="$1"; shift
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") || return 1
  printf '%s' "$requested" | jq --argjson logins "$logins_json" \
    '[$logins[] | ascii_downcase | sub("\\[bot\\]$"; "")] as $want
     | any(.[]; . as $have | $want | index($have) != null)'
}

# Direct execution: a guarded CLI (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  _in_flight=$(copilot_run_in_flight "$1" "$2" "$3")
  python3 -c 'import json, sys; print(json.dumps({"in_flight": sys.argv[1] == "true"}))' "$_in_flight"
fi
