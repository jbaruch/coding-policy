#!/usr/bin/env bash
# Settle superseded legacy bot gates.
#
# Compatibility interface for external release callers and legacy review gates.
# coding-policy's own workflow now posts through the fleet policy App, which
# can APPROVE. Ordinary App approvals need no dismissal. Existing open PRs can
# still carry github-actions[bot] requests that the new identity cannot replace.
# Only on jbaruch/coding-policy, a latest legacy CHANGES_REQUESTED can also be
# cleared by a later APPROVED from coding-policy-fleet-reviewer[bot] on the
# current head of an OPEN PR. COMMENTED, dismissed or old-head App reviews do not
# authorize this migration. Other repositories keep the same-bot contract.
#
# On this repo and consumer repos the reviewer is the central fleet App
# `coding-policy-fleet-reviewer[bot]` (coding-policy#202), which CAN APPROVE, so
# its re-approval supersedes its own earlier CHANGES_REQUESTED with no dismissal
# needed. It is intentionally NOT in GATING_BOTS below — a bot that can approve
# never needs its review dismissed.
#
# Decision, per gating bot (see GATING_BOTS below):
#   - latest review state == CHANGES_REQUESTED => leave it; the bot is
#     currently requesting changes, nothing to dismiss.
#   - latest review state == COMMENTED or APPROVED (a fresh all-clear) =>
#     dismiss every EARLIER review from that bot still in CHANGES_REQUESTED.
#   - any other latest state => no-op. An earlier active request stays put.
# Reviews already in DISMISSED state are skipped, so re-running is a no-op
# (idempotent per rules/file-hygiene.md).
#
# Usage: dismiss-stale-reviews.sh <owner> <repo> <pr-number>
# Out:   one JSON object on stdout:
#   {
#     "pr_number": N,
#     "dismissed": [{"login": "...", "review_id": N, "commit_id": "..."}],
#     "left_active": [{"login": "...", "review_id": N}]  // latest is still CHANGES_REQUESTED
#   }
# Exit:  0 on successful query (including no-op); non-zero with a stderr
#        diagnostic on API/argument failure.

set -euo pipefail

# Legacy identities retained for declared external release callers. On
# coding-policy only github-actions[bot] has ordinary migration eligibility.
# An array iterated with quoted expansion, NOT a space-string: the `[bot]` suffix is
# a glob bracket, so an unquoted `for login in $GATING_BOTS` would rewrite
# the token against a matching filename in the release checkout (e.g.
# `github-actionsb`) and silently miss the review.
GATING_BOTS=("github-actions[bot]" "copilot-pull-request-reviewer[bot]")

# Fixed dismissal message — a dismissal records who/why on the PR timeline.
DISMISS_MESSAGE="Superseded by a later all-clear review from the same bot — dismissed by the release skill so the stale request stops gating the merge."

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# All reviews by one login, chronological (GitHub returns ascending by
# submitted_at). `--paginate` is mandatory so a PR with >30 reviews doesn't
# truncate and hide the latest verdict — same discipline as
# poll-pr-reviews.sh.
reviews_by() {
  local owner="$1" repo="$2" pr="$3" login="$4"
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" \
    | jq -s --arg login "$login" \
        '(add // []) | [.[] | select(.user.login == $login) | {id, state, commit_id, submitted_at}]'
}

# A non-zero PUT is not proof the dismissal failed: `gh` can exit non-zero
# on an unparseable response after GitHub already dismissed the review. On
# a failed PUT, read the review back; DISMISSED is success with a warning,
# any other state (or a failed read) is a failure carrying the PUT's error.
dismiss_review() {
  local owner="$1" repo="$2" pr="$3" review_id="$4"
  local put_err state=""
  if put_err=$(gh api -X PUT \
      "repos/${owner}/${repo}/pulls/${pr}/reviews/${review_id}/dismissals" \
      -f message="$DISMISS_MESSAGE" \
      -f event="DISMISS" 2>&1 >/dev/null); then
    return 0
  fi
  if state=$(gh api "repos/${owner}/${repo}/pulls/${pr}/reviews/${review_id}" | jq -r '.state') \
      && [[ "$state" == "DISMISSED" ]]; then
    echo "warning: dismissal response for review ${review_id} on ${owner}/${repo}#${pr} could not be parsed (${put_err}); the review reads back DISMISSED, treating it as dismissed" >&2
    return 0
  fi
  echo "error: dismissal PUT for review ${review_id} on ${owner}/${repo}#${pr} failed (${put_err}); the review reads back ${state:-unreadable} — check 'gh auth status' and the review on the PR, then re-run" >&2
  return 1
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr="$3"
  if [[ -z "$owner" || -z "$repo" || -z "$pr" ]]; then
    echo "error: <owner>, <repo>, and <pr-number> are all required and must be non-empty" >&2
    exit 2
  fi

  local dismissed="[]" left_active="[]"
  local login reviews latest_state stale migration_head pr_info app_reviews
  local default_message="$DISMISS_MESSAGE" DISMISS_MESSAGE="$DISMISS_MESSAGE"

  for login in "${GATING_BOTS[@]}"; do
    if [[ "$owner/$repo" == jbaruch/coding-policy && "$login" == 'copilot-pull-request-reviewer[bot]' ]]; then
      continue
    fi
    DISMISS_MESSAGE="$default_message"
    migration_head=""
    reviews=$(reviews_by "$owner" "$repo" "$pr" "$login") \
      || { echo "error: failed to fetch reviews for ${login} on ${owner}/${repo}#${pr} — run 'gh auth status' to verify auth, then retry" >&2; exit 1; }

    # No reviews from this bot yet — nothing to do.
    [[ "$(jq 'length' <<<"$reviews")" == "0" ]] && continue

    latest_state=$(jq -r '.[-1].state' <<<"$reviews")

    if [[ "$latest_state" == CHANGES_REQUESTED ]]; then
      stale=""
      # Migration may clear the latest legacy request as well as earlier ones.
      if [[ "$owner/$repo" == jbaruch/coding-policy && "$login" == 'github-actions[bot]' ]]; then
        stale=$(jq -c '.[] | select(.state == "CHANGES_REQUESTED")' <<<"$reviews")
        DISMISS_MESSAGE="Superseded by a later current-head policy App approval during coding-policy's github-actions[bot] migration — dismissed by the release skill."
      fi
    elif [[ "$latest_state" == COMMENTED || "$latest_state" == APPROVED ]]; then
      stale=$(jq -c '.[:-1][] | select(.state == "CHANGES_REQUESTED")' <<<"$reviews")
    else
      continue
    fi

    local row rid cid
    while IFS= read -r row; do
      [[ -z "$row" ]] && continue
      rid=$(jq -r '.id' <<<"$row")
      cid=$(jq -r '.commit_id' <<<"$row")
      if [[ "$latest_state" == CHANGES_REQUESTED ]]; then
        # Refresh authorization for each migration PUT. Read the PR last and
        # stop if its head differs from the one that started the sweep.
        app_reviews=$(reviews_by "$owner" "$repo" "$pr" 'coding-policy-fleet-reviewer[bot]') \
          || { echo "error: cannot read migration policy App reviews — restore GitHub API access and retry" >&2; exit 1; }
        pr_info=$(gh api "repos/${owner}/${repo}/pulls/${pr}") \
          || { echo "error: cannot read migration PR state/head — check 'gh auth status' and retry" >&2; exit 1; }
        migration_head=$(jq -nr --argjson pr "$pr_info" --argjson app "$app_reviews" \
          --argjson legacy "$reviews" --arg sweep_head "$migration_head" '
          $app[-1] as $approval | $legacy[-1] as $old |
          if $pr.state == "open" and ($pr.head.sha | type == "string" and length > 0)
            and ($sweep_head == "" or $pr.head.sha == $sweep_head)
            and $approval.state == "APPROVED" and $approval.commit_id == $pr.head.sha
            and ($approval.submitted_at > $old.submitted_at)
          then $pr.head.sha else "" end') \
          || { echo "error: invalid migration review evidence — inspect the PR and review API responses, then retry" >&2; exit 1; }
        [[ -n "$migration_head" ]] || break
      fi
      dismiss_review "$owner" "$repo" "$pr" "$rid" \
        || { echo "error: failed to dismiss review ${rid} by ${login} on ${owner}/${repo}#${pr}" >&2; exit 1; }
      dismissed=$(jq --arg login "$login" --arg cid "$cid" \
        '. + [{login: $login, review_id: '"$rid"', commit_id: $cid}]' <<<"$dismissed")
    done <<<"$stale"

    if [[ "$latest_state" == CHANGES_REQUESTED && -z "$migration_head" ]]; then
      left_active=$(jq --arg login "$login" \
        '. + [{login: $login, review_id: '"$(jq '.[-1].id' <<<"$reviews")"'}]' <<<"$left_active")
    fi
  done

  jq -n \
    --argjson pr "$pr" \
    --argjson dismissed "$dismissed" \
    --argjson left_active "$left_active" \
    '{pr_number: $pr, dismissed: $dismissed, left_active: $left_active}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
