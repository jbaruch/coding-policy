#!/usr/bin/env bash
# Snapshot CI status, bot review states, and inline comment counts for a PR.
# Non-blocking — call repeatedly to observe transitions.
#
# Usage: poll-pr-reviews.sh <owner> <repo> <pr-number>
# Out:   one JSON object on stdout with the schema below.
# Exit:  0 on successful query; non-zero with stderr diagnostic on failure
#
# Schema:
#   {
#     "pr_number": N,
#     "ci":   {"status": "pending|success|failure|none", "checks": [...]},
#     "reviews": {
#       "gh_aw":   {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null"},
#       "copilot": {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null"}
#     },
#     "inline_comments": {"gh_aw": N, "copilot": N},
#     "merge_state": {"status": "CLEAN|DIRTY|BLOCKED|BEHIND|UNSTABLE|...",
#                     "mergeable": "MERGEABLE|CONFLICTING|UNKNOWN"}
#   }
#
# `merge_state.status == "DIRTY"` / `mergeable == "CONFLICTING"` means GitHub
# couldn't create `refs/pull/N/merge` and silently skipped `pull_request:`
# workflows — agent should surface a rebase recommendation rather than keep
# polling `ci.status: none`.

set -euo pipefail

latest_review_by() {
  local owner="$1" repo="$2" pr="$3" login="$4"
  gh api "repos/${owner}/${repo}/pulls/${pr}/reviews" \
    --jq "[.[] | select(.user.login == \"${login}\")] | last
          | if . == null then {state: \"none\", submitted_at: null}
            else {state, submitted_at} end"
}

toplevel_comments_by() {
  local owner="$1" repo="$2" pr="$3" login="$4"
  gh api "repos/${owner}/${repo}/pulls/${pr}/comments" \
    --jq "[.[] | select(.user.login == \"${login}\") | select(.in_reply_to_id == null)] | length"
}

fetch_merge_state() {
  local owner="$1" repo="$2" pr="$3"
  gh pr view "$pr" --repo "${owner}/${repo}" --json mergeStateStatus,mergeable \
    | jq -c '{status: .mergeStateStatus, mergeable: .mergeable}'
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr_number="$3"

  # gh pr checks exits 8 when no checks are configured — distinguish that from real errors.
  local checks_json checks_raw rc=0
  checks_raw=$(gh pr checks "$pr_number" --repo "${owner}/${repo}" --json name,bucket 2>&1) || rc=$?
  if [[ $rc -eq 0 ]]; then
    checks_json="$checks_raw"
  elif [[ $rc -eq 8 ]] || echo "$checks_raw" | grep -qi "no check"; then
    checks_json='[]'
  else
    echo "error: gh pr checks failed (rc=${rc}): ${checks_raw}" >&2
    exit 1
  fi

  local ci_status
  ci_status=$(echo "$checks_json" | jq -r '
    if (. | length) == 0 then "none"
    elif any(.bucket == "fail" or .bucket == "cancel") then "failure"
    elif any(.bucket == "pending") then "pending"
    else "success" end
  ')

  local merge_state
  merge_state=$(fetch_merge_state "$owner" "$repo" "$pr_number") \
    || { echo "error: failed to fetch merge state for ${owner}/${repo}#${pr_number} — run 'gh auth status' to verify auth, then retry 'gh pr view ${pr_number} --repo ${owner}/${repo} --json mergeStateStatus,mergeable' to inspect the failing call directly" >&2; exit 1; }

  local gh_aw_review copilot_review gh_aw_comments copilot_comments
  gh_aw_review=$(latest_review_by   "$owner" "$repo" "$pr_number" "github-actions[bot]") \
    || { echo "error: failed to fetch gh-aw review state" >&2; exit 1; }
  copilot_review=$(latest_review_by "$owner" "$repo" "$pr_number" "copilot-pull-request-reviewer[bot]") \
    || { echo "error: failed to fetch Copilot review state" >&2; exit 1; }
  gh_aw_comments=$(toplevel_comments_by   "$owner" "$repo" "$pr_number" "github-actions[bot]") \
    || { echo "error: failed to count gh-aw inline comments" >&2; exit 1; }
  copilot_comments=$(toplevel_comments_by "$owner" "$repo" "$pr_number" "copilot-pull-request-reviewer[bot]") \
    || { echo "error: failed to count Copilot inline comments" >&2; exit 1; }

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg ci_status "$ci_status" \
    --argjson checks "$checks_json" \
    --argjson gh_aw "$gh_aw_review" \
    --argjson copilot "$copilot_review" \
    --argjson gh_aw_comments "$gh_aw_comments" \
    --argjson copilot_comments "$copilot_comments" \
    --argjson merge_state "$merge_state" \
    '{
      pr_number: $pr_number,
      ci: {status: $ci_status, checks: $checks},
      reviews: {gh_aw: $gh_aw, copilot: $copilot},
      inline_comments: {gh_aw: $gh_aw_comments, copilot: $copilot_comments},
      merge_state: $merge_state
    }'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
