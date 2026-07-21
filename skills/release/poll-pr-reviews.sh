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
#       "codex":   {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null"},
#       "copilot": {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null"}
#     },
#     "inline_comments": {"codex": N, "copilot": N},
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
# and its inline comments under the same login, and the policy reviewer's login
# depends on WHICH repo the PR is in:
#
#   surface          Policy reviewer                       Copilot
#   ---------------  ------------------------------------  ----------------------------------
#   review           github-actions[bot] (coding-policy's  copilot-pull-request-reviewer[bot]
#                    own PRs) OR
#                    coding-policy-fleet-reviewer[bot]
#                    (consumer repos)
#   inline comment   same login as its review              Copilot
#
# The policy reviewer runs two ways: in coding-policy itself as a GitHub Actions
# workflow (review-codex.yml, Codex CLI) submitting with the workflow's
# GITHUB_TOKEN, so its author is `github-actions[bot]`; in every consumer repo as
# the central fleet App (coding-policy#202), submitting as
# `coding-policy-fleet-reviewer[bot]`. A given PR is reviewed by exactly one of
# them today, so the watcher must resolve the policy reviewer across BOTH logins
# — one that only knew `github-actions[bot]` sat blind at `none` on every
# consumer PR the fleet App reviewed. `latest_review_by` aggregates fail-safe
# (each login's own latest, CHANGES_REQUESTED wins) so a hypothetical both-logins
# PR can never mask an active block.
#
# Counting Copilot's comments against its REVIEW login matches nothing, so
# `inline_comments.copilot` reads 0 on every PR — which vacuously satisfies the
# release skill's Step 7 "every inline comment has a reply" merge gate and lets
# a real Copilot finding merge unanswered. Comment counting therefore matches a
# SET of logins per reviewer. A comment carries exactly one author, so listing
# multiple logins cannot double-count.
CODEX_REVIEW_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")
COPILOT_REVIEW_LOGIN="copilot-pull-request-reviewer[bot]"
CODEX_COMMENT_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")
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
# Variadic on login so one reviewer's multiple identities collapse to a single
# verdict — the policy reviewer is `github-actions[bot]` on coding-policy's own
# PRs and `coding-policy-fleet-reviewer[bot]` on consumer repos (see the login
# table above). A given PR carries reviews from only one of them today, but this
# is a MERGE GATE: aggregate fail-safe rather than assume. Take each login's
# OWN latest review, then if ANY of those is CHANGES_REQUESTED surface that
# (an active block from one identity must never be masked by a later clean
# review from another); otherwise surface the newest among them.
latest_review_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for review lookup" >&2; return 1; }
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" \
    | jq -s --argjson logins "$logins_json" '
        (add // [])
        | [.[] | select(.user.login | IN($logins[]))]
        | (group_by(.user.login) | map(last)) as $per_login_latest
        | ( ($per_login_latest | map(select(.state == "CHANGES_REQUESTED")) | first)
            // ($per_login_latest | sort_by(.submitted_at) | last) )
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

  local codex_review copilot_review codex_comments copilot_comments
  codex_review=$(latest_review_by   "$owner" "$repo" "$pr_number" "${CODEX_REVIEW_LOGINS[@]}") \
    || { echo "error: failed to fetch Codex review state" >&2; exit 1; }
  copilot_review=$(latest_review_by "$owner" "$repo" "$pr_number" "$COPILOT_REVIEW_LOGIN") \
    || { echo "error: failed to fetch Copilot review state" >&2; exit 1; }
  codex_comments=$(toplevel_comments_by   "$owner" "$repo" "$pr_number" "${CODEX_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Codex inline comments" >&2; exit 1; }
  copilot_comments=$(toplevel_comments_by "$owner" "$repo" "$pr_number" "${COPILOT_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Copilot inline comments" >&2; exit 1; }

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg ci_status "$ci_status" \
    --argjson checks "$checks_json" \
    --argjson codex "$codex_review" \
    --argjson copilot "$copilot_review" \
    --argjson codex_comments "$codex_comments" \
    --argjson copilot_comments "$copilot_comments" \
    --argjson merge_state "$merge_state" \
    '{
      pr_number: $pr_number,
      ci: {status: $ci_status, checks: $checks},
      reviews: {codex: $codex, copilot: $copilot},
      inline_comments: {codex: $codex_comments, copilot: $copilot_comments},
      merge_state: $merge_state
    }'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
