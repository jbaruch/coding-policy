#!/usr/bin/env bash
# Snapshot CI, mergeability, exact-head Codex/Claude verdicts and all feedback.
# Usage: poll-pr-reviews.sh OWNER REPO PR
# stdout: {pr_number, head_sha, ci:{status,checks}, reviews:{codex,claude,copilot},
#          review_bodies, inline_comments, inline_comment_bodies, merge_state}.
# Review fields: state, submitted_at, body, commit_id, stale, requested:false.
# Codex may be RULED after its sanctioned policy dismissal; stale reviews become
# none. Claude has its own App identity. Copilot compatibility fields are always
# none plus historical comment counts, with no Copilot service lookup. All bodies
# and inline comments are still read from GitHub, from every author and page.
# Exit 0: snapshot; nonzero: stderr diagnostic, never an inferred clean review.

set -euo pipefail

# Preserve directory names ending in newlines while resolving the shipped helper.
case "${BASH_SOURCE[0]}" in
  */*) _ppr_src="${BASH_SOURCE[0]%/*}" ;;
  *) _ppr_src=. ;;
esac
if ! _ppr_dir="$(CDPATH='' cd -- "${_ppr_src:-/}" && pwd && printf x)"; then
  echo "error: cannot enter the script directory ${_ppr_src:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
  exit 2
fi
_ppr_dir="${_ppr_dir%x}"
_ppr_dir="${_ppr_dir%$'\n'}"
# shellcheck source=skills/release/review-lanes.sh
if ! source "${_ppr_dir}/review-lanes.sh"; then
  echo "error: cannot load ${_ppr_dir}/review-lanes.sh — restore the release skill tree by re-cloning or reinstalling the plugin, then retry" >&2
  exit 2
fi

# Policy has a self-review identity and a central fleet identity. Claude's
# distinct identity is owned by the shipped helper loaded above.
CODEX_REVIEW_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")
CODEX_COMMENT_LOGINS=("github-actions[bot]" "coding-policy-fleet-reviewer[bot]")

# Slurp a paginated `gh api` array into one JSON array, dropping any element
# that is not an object. A degraded or partial response can carry a string
# element where an object belongs; indexing it (`.in_reply_to_id`,
# `.user.login`) aborts jq mid-filter and fails the whole poll, which
# watch-pr-reviews.sh then burns its full attempt budget on and reports as
# `pending_at_budget` — "no reviewer posted" when the truth is "the tool
# broke" (issue #300). A non-object element carries neither a verdict nor a
# comment, so dropping it loses nothing; the warning keeps the drop visible
# rather than silent (rules/error-handling.md — best-effort work that
# continues past a failure warns, never nothing).
#
# `--paginate` is mandatory: GitHub's default per-page is 30, and a PR with
# more than that many reviews/comments would otherwise return only the first
# page. `--jq` is incompatible with `--paginate` here (it applies per page,
# not across the stream), so slurp every page into one array with `jq -s`
# before filtering. `per_page=100` is the API maximum and keeps request
# volume bounded.
slurp_api_array() {
  local endpoint="$1" what="$2" payload malformed
  payload=$(gh api --paginate "$endpoint" | jq -s '(add // [])') \
    || { echo "error: failed to fetch ${what} from ${endpoint}" >&2; return 1; }
  malformed=$(printf '%s' "$payload" | jq '[.[] | select(type != "object")] | length') \
    || { echo "error: failed to inspect the ${what} payload from ${endpoint}" >&2; return 1; }
  if (( malformed > 0 )); then
    echo "warning: ${endpoint} returned ${malformed} non-object element(s) in the ${what} payload — dropped; re-run poll-pr-reviews.sh if the snapshot looks wrong, and inspect the response with 'gh api --paginate ${endpoint}' if it persists" >&2
  fi
  printf '%s' "$payload" | jq '[.[] | select(type == "object")]'
}

# Missing a page would let the `last` filter pick the last entry on page 1 —
# not the actual latest review — and the gate could approve a merge against
# stale data; slurp_api_array above owns the pagination.
# Variadic on login so one reviewer's multiple identities collapse to a single
# verdict — the policy reviewer is `github-actions[bot]` on coding-policy's own
# PRs and `coding-policy-fleet-reviewer[bot]` on consumer repos (see the login
# table above). A given PR carries reviews from only one of them today, but this
# is a MERGE GATE: aggregate fail-safe rather than assume. Take each login's
# OWN latest review, then if ANY of those is CHANGES_REQUESTED surface that
# (an active block from one identity must never be masked by a later clean
# review from another); otherwise surface the newest among them.
# `max_by(.submitted_at)` for the per-login latest, NOT `last`: jq `group_by`
# sorts only by the group key and preserves input order within a group, so
# `last` returns the newest review only when the API happened to return that
# login's reviews in chronological order. A merge gate must not rest on that
# assumption — a login whose re-review came back out of order would gate on a
# superseded verdict — so select the max by submitted_at explicitly.
latest_review_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json lane="${REVIEW_LANE:-policy}"
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for review lookup" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" "review" \
    | jq --arg lane "$lane" --argjson logins "$logins_json" "$(review_lanes_jq)"'
        [.[] | select(.user.login | IN($logins[])) | select(in_review_lane($lane))]
        | (group_by(.user.login) | map(max_by(.submitted_at))) as $per_login_latest
        | ( ($per_login_latest | map(select(.state == "CHANGES_REQUESTED")) | first)
            // ($per_login_latest | sort_by(.submitted_at) | last) )
        # Normalize to the documented schema states. GitHub also emits
        # DISMISSED and PENDING; neither is a live verdict, but the watcher
        # treats any non-"none" state as "a bot posted" and would let a
        # dismissed/pending review satisfy the ready gate. Collapse anything
        # outside {APPROVED, CHANGES_REQUESTED, COMMENTED} to "none" (absent),
        # keeping submitted_at/body/commit_id visible for diagnosis.
        # A DISMISSED latest keeps its id under `_dismissed_review_id` so
        # resolve_ruled_dismissal can read its dismissal message; main strips
        # the field before output.
        | if . == null then {state: "none", submitted_at: null, body: null, commit_id: null}
          else {state: (if (.state | IN("APPROVED", "CHANGES_REQUESTED", "COMMENTED")) then .state else "none" end),
                submitted_at, body, commit_id}
               + (if .state == "DISMISSED" then {_dismissed_review_id: .id} else {} end) end'
}

# A policy review dismissed through dismiss-ruled-review.sh reads RULED: its
# `review_dismissed` timeline event carries a message starting RULED_MARKER.
# Any other dismissal stays "none". Head binding is resolve_review_against_head's
# job — a RULED review on an older commit collapses to "none" there.
# tests/release/tests/test_dismiss_ruled_review.sh feeds the message
# dismiss-ruled-review.sh sends through this resolver end to end.
RULED_MARKER="JUDGE-RULED:"

resolve_ruled_dismissal() {
  local owner="$1" repo="$2" pr="$3" review="$4" review_id
  review_id=$(printf '%s' "$review" | jq -r '._dismissed_review_id // empty')
  if [[ -z "$review_id" ]]; then
    printf '%s' "$review" | jq 'del(._dismissed_review_id)'
    return 0
  fi
  slurp_api_array "repos/${owner}/${repo}/issues/${pr}/timeline?per_page=100" "timeline event" \
    | jq --argjson review "$review" --argjson id "$review_id" --arg marker "$RULED_MARKER" '
        [.[] | select(.event == "review_dismissed")
             | select(.dismissed_review.review_id == $id)
             | (.dismissed_review.dismissal_message // "")]
        | (if any(.[]; startswith($marker)) then ($review | .state = "RULED") else $review end)
        | del(._dismissed_review_id)'
}

# Resolve a latest_review_by result against the PR head SHA. A review's verdict
# approves only the commit it reviewed (rules/autonomous-shipping.md), so a
# review whose `commit_id` is not the head collapses to state "none" — absent,
# not clean — while its verdict stays visible for diagnosis under a `stale`
# flag. `head` is always the live headRefOid in production (main asserts it
# non-empty); an empty head would mark every real review stale, which is why
# main refuses to proceed without one rather than silently voiding the gate.
#
# NOTE on ordering vs the CHANGES_REQUESTED-wins aggregation in
# latest_review_by: today exactly one policy-reviewer identity posts per PR, so
# aggregation collapses to that one review and resolving here is equivalent to
# resolving before aggregation. The hypothetical mixed-freshness-two-logins PR
# does not occur (see the login table above); if it ever does, fold this
# binding into the jq above so stale reviews drop before the CR-wins pick.
resolve_review_against_head() {
  local review="$1" head="$2"
  printf '%s' "$review" | jq --arg head "$head" '
    if .state == "none" then . + {stale: false}
    elif .commit_id == $head then . + {stale: false}
    else {state: "none", submitted_at, body, commit_id, stale: true} end'
}

# Count top-level (non-reply) inline comments authored by ANY of <login...>.
# Variadic on login so one reviewer's multiple author identities collapse to a
# single count — see the login table at the top of this file.
toplevel_comments_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for comment count" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/comments?per_page=100" "inline comment" \
    | jq --argjson logins "$logins_json" '
        [.[] | select(.in_reply_to_id == null) | select(.user.login | IN($logins[]))]
        | length'
}

# Also carries the PR head SHA (headRefOid) so review verdicts can be bound to
# the commit they reviewed — folded into this call rather than a second
# `gh pr view` so head and merge-state come from one consistent read. main
# splits head_sha to a top-level field and keeps merge_state as {status,
# mergeable}.
fetch_merge_state() {
  local owner="$1" repo="$2" pr="$3"
  gh pr view "$pr" --repo "${owner}/${repo}" --json mergeStateStatus,mergeable,headRefOid \
    | jq -c '{status: .mergeStateStatus, mergeable: .mergeable, head_sha: .headRefOid}'
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr_number="$3"
  local tool
  for tool in gh jq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "error: ${tool} is not installed — install it with your package manager, authenticate gh, then retry" >&2
      exit 2
    fi
  done

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

  # Supersession is scoped to the check name on the current head. An unreplaced
  # cancellation stays pending; a same-name live check supplies its result.
  # A real failure takes precedence. Skipped checks keep their existing semantics.
  local ci_status
  ci_status=$(echo "$checks_json" | jq -r '
    (map(select(.bucket != "cancel"))) as $live
    | (map(select(.bucket == "cancel") | .name) - ($live | map(.name))) as $unreplaced
    | if (. | length) == 0 then "none"
      elif ($live | any(.bucket == "fail")) then "failure"
      elif ($live | any(.bucket == "pending")) then "pending"
      elif ($unreplaced | length) > 0 then "pending"
      elif ($live | length) == 0 then "pending"
      else "success" end
  ')

  local merge_state
  merge_state=$(fetch_merge_state "$owner" "$repo" "$pr_number") \
    || { echo "error: failed to fetch merge state for ${owner}/${repo}#${pr_number} — run 'gh auth status' to verify auth, then retry 'gh pr view ${pr_number} --repo ${owner}/${repo} --json mergeStateStatus,mergeable,headRefOid' to inspect the failing call directly" >&2; exit 1; }

  # Head SHA binds every review verdict to the commit it reviewed. Refuse to
  # proceed without it: an empty head would mark every real review stale and
  # silently void the review gate — the opposite of the fail-safe intent.
  local head_sha
  head_sha=$(printf '%s' "$merge_state" | jq -r '.head_sha // empty')
  if [[ -z "$head_sha" ]]; then
    echo "error: 'gh pr view ${pr_number} --repo ${owner}/${repo}' returned no headRefOid — cannot bind review verdicts to the PR head; verify the PR number and 'gh auth status', then retry" >&2
    exit 1
  fi

  local codex_review claude_review copilot_review codex_comments copilot_comments comment_bodies review_bodies
  codex_review=$(REVIEW_LANE=policy latest_review_by   "$owner" "$repo" "$pr_number" "${CODEX_REVIEW_LOGINS[@]}") \
    || { echo "error: failed to fetch Codex review state" >&2; exit 1; }
  claude_review=$(REVIEW_LANE=quality latest_review_by "$owner" "$repo" "$pr_number" "$CLAUDE_REVIEW_LOGIN") \
    || { echo "error: failed to fetch Claude review state" >&2; exit 1; }
  claude_review=$(printf '%s' "$claude_review" | jq 'del(._dismissed_review_id)')
  claude_review=$(resolve_review_against_head "$claude_review" "$head_sha")
  claude_review=$(printf '%s' "$claude_review" | jq '. + {requested: false}')
  # Only the policy reviewer's review is ever ruled-dismissed.
  codex_review=$(resolve_ruled_dismissal "$owner" "$repo" "$pr_number" "$codex_review") \
    || { echo "error: failed to read the dismissal of the policy review on ${owner}/${repo}#${pr_number} — inspect 'gh api --paginate repos/${owner}/${repo}/issues/${pr_number}/timeline', then retry" >&2; exit 1; }
  # Resolve each verdict against head — stale reviews collapse to "none".
  codex_review=$(resolve_review_against_head   "$codex_review"   "$head_sha")
  codex_review=$(printf '%s' "$codex_review" | jq '. + {requested: false}')
  codex_comments=$(toplevel_comments_by   "$owner" "$repo" "$pr_number" "${CODEX_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Codex inline comments" >&2; exit 1; }
  copilot_review='{"state":"none","submitted_at":null,"body":null,"commit_id":null,"stale":false,"requested":false}'
  copilot_comments=0
  comment_bodies=$(slurp_api_array "repos/${owner}/${repo}/pulls/${pr_number}/comments?per_page=100" "inline comment" \
    | jq '[.[] | {id, in_reply_to_id, path, line, body, html_url, author: .user.login}]') \
    || { echo "error: failed to read inline comment bodies — restore GitHub API access and rerun this snapshot before merging" >&2; exit 1; }
  # Count legacy feedback from the same all-author snapshot, without a Copilot
  # request, run, timeline or reviewer-specific API lookup.
  copilot_comments=$(printf '%s' "$comment_bodies" | jq '[.[] | select(.in_reply_to_id == null)
    | select(.author | IN("Copilot", "copilot-pull-request-reviewer[bot]"))] | length')
  review_bodies=$(slurp_api_array "repos/${owner}/${repo}/pulls/${pr_number}/reviews?per_page=100" "review" \
    | jq '[.[] | select(.body != null and .body != "")
                   | {id, state, submitted_at, commit_id, body, html_url, author: .user.login}]') \
    || { echo "error: failed to read all review bodies — restore GitHub API access and rerun this snapshot before merging" >&2; exit 1; }

  # Review text can exceed a platform's per-argument limit. Stream every
  # potentially large JSON payload instead of passing it through --argjson.
  printf '%s\n' "$checks_json" "$codex_review" "$copilot_review" \
    "$comment_bodies" "$review_bodies" "$merge_state" "$claude_review" | jq -s \
    --argjson pr_number "$pr_number" \
    --arg head_sha "$head_sha" \
    --arg ci_status "$ci_status" \
    --argjson codex_comments "$codex_comments" \
    --argjson copilot_comments "$copilot_comments" \
    '{
      pr_number: $pr_number,
      head_sha: $head_sha,
      ci: {status: $ci_status, checks: .[0]},
      reviews: {codex: .[1], claude: .[6], copilot: .[2]},
      review_bodies: .[4],
      inline_comments: {codex: $codex_comments, copilot: $copilot_comments},
      inline_comment_bodies: .[3],
      merge_state: (.[5] | {status, mergeable})
    }'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
