#!/usr/bin/env bash
# Snapshot CI status, bot review states + bodies, and inline comment counts for a PR.
# Non-blocking — call repeatedly to observe transitions.
#
# Usage: poll-pr-reviews.sh <owner> <repo> <pr-number>
# Out:   one JSON object on stdout with the schema below.
# Exit:  0 on successful query; non-zero with stderr diagnostic on failure
#
# `reviews.*.body` carries the full review body text — a review's state classifies
# whether it gates the merge, not whether its body must be read. A COMMENTED review
# with zero inline comments still carries a body (see rules/reviewer-feedback-reading.md).
#
# Schema:
#   {
#     "pr_number": N,
#     "ci":   {"status": "pending|success|failure|none", "checks": [...]},
#     "reviews": {
#       "gh_aw":   {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null"},
#       "copilot": {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null"}
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

# Bot logins, by surface. A reviewer does NOT necessarily author its reviews
# and its inline comments under the same login:
#
#   surface          gh-aw                  Copilot
#   ---------------  ---------------------  --------------------------------
#   review           github-actions[bot]    copilot-pull-request-reviewer[bot]
#   inline comment   github-actions[bot]    Copilot
#
# Counting Copilot's comments against its REVIEW login matches nothing, so
# `inline_comments.copilot` reads 0 on every PR — which vacuously satisfies the
# release skill's Step 7 "every inline comment has a reply" merge gate and lets
# a real Copilot finding merge unanswered. Comment counting therefore matches a
# SET of logins per reviewer. A comment carries exactly one author, so listing
# both logins cannot double-count.
GH_AW_REVIEW_LOGIN="github-actions[bot]"
COPILOT_REVIEW_LOGIN="copilot-pull-request-reviewer[bot]"
GH_AW_COMMENT_LOGINS=("github-actions[bot]")
COPILOT_COMMENT_LOGINS=("Copilot" "copilot-pull-request-reviewer[bot]")

# `--paginate` is mandatory: GitHub's default per-page is 30, and a PR
# with more than that many reviews/comments would otherwise return only
# the first page. The script's `last` filter would then pick the last
# entry on page 1 — not the actual latest review — and the gate could
# approve a merge against stale data. `--jq` is incompatible with
# `--paginate` here (it applies per page, not across the stream), so
# pipe the raw paginated output through `jq -s 'add | ...'` to slurp
# every page into one array before filtering. `per_page=100` is the API
# maximum and keeps request volume bounded.
latest_review_by() {
  local owner="$1" repo="$2" pr="$3" login="$4"
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" \
    | jq -s --arg login "$login" '
        (add // [])
        | [.[] | select(.user.login == $login)]
        | last
        | if . == null then {state: "none", submitted_at: null, body: null}
          else {state, submitted_at, body} end'
}

# Count top-level (non-reply) inline comments authored by ANY of <login...>.
# Variadic on login so one reviewer's multiple author identities collapse to a
# single count — see the login table at the top of this file.
toplevel_comments_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for comment count" >&2; return 1; }
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/comments?per_page=100" \
    | jq -s --argjson logins "$logins_json" '
        (add // [])
        | [.[] | select(.in_reply_to_id == null) | select(.user.login | IN($logins[]))]
        | length'
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
  # Here-string, not `echo | grep -qi`: `-q` makes grep exit at the first
  # match and close the pipe on a still-writing echo, so under `pipefail` the
  # pipeline can carry echo's SIGPIPE (141) while grep matched — turning this
  # no-checks branch false and routing a valid state into the error exit
  # below (rules/error-handling.md Shell Error Handling).
  elif [[ $rc -eq 8 ]] || grep -qi "no check" <<<"$checks_raw"; then
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
  gh_aw_review=$(latest_review_by   "$owner" "$repo" "$pr_number" "$GH_AW_REVIEW_LOGIN") \
    || { echo "error: failed to fetch gh-aw review state" >&2; exit 1; }
  copilot_review=$(latest_review_by "$owner" "$repo" "$pr_number" "$COPILOT_REVIEW_LOGIN") \
    || { echo "error: failed to fetch Copilot review state" >&2; exit 1; }
  gh_aw_comments=$(toplevel_comments_by   "$owner" "$repo" "$pr_number" "${GH_AW_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count gh-aw inline comments" >&2; exit 1; }
  copilot_comments=$(toplevel_comments_by "$owner" "$repo" "$pr_number" "${COPILOT_COMMENT_LOGINS[@]}") \
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
