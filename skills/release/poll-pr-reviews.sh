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
#     "head_sha": "<PR head commit SHA>",
#     "ci":   {"status": "pending|success|failure|none", "checks": [...]},
#     "reviews": {
#       "codex":   {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|RULED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null",
#                   "commit_id": "<SHA the review is bound to>|null",
#                   "stale": bool, "requested": bool},
#       "copilot": {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null",
#                   "commit_id": "<SHA the review is bound to>|null",
#                   "stale": bool, "requested": bool}
#     },
#     "inline_comments": {"codex": N, "copilot": N},
#     "merge_state": {"status": "CLEAN|DIRTY|BLOCKED|BEHIND|UNSTABLE|...",
#                     "mergeable": "MERGEABLE|CONFLICTING|UNKNOWN"}
#   }
#
# A review's `.state` is resolved AGAINST `head_sha`: a review whose
# `commit_id` is not the current head has not reviewed the current code, so
# its `.state` collapses to "none" (absent, not clean) and `.stale` is true —
# the rule in rules/review-severity.md / autonomous-shipping.md that a verdict
# approves only the commit it reviewed. The pre-resolution verdict stays
# visible in `submitted_at` / `body` / `commit_id` so a reader can tell "never
# reviewed" (state none, stale false) from "reviewed an older commit" (state
# none, stale true). Binding both symptoms to head fixes them together: a
# stale CHANGES_REQUESTED no longer false-reds a fix no reviewer has seen, and
# a stale COMMENTED/APPROVED no longer false-readies unreviewed code (#186).
#
# `RULED` (policy reviewer only): the latest policy review is on the head and
# was dismissed by skills/release/dismiss-ruled-review.sh — its dismissal
# message starts with RULED_MARKER, meaning a weighing ruling covers every
# blocking finding in it (rules/ci-safety.md Judge-Ruled-Review Dismissal
# Carve-Out). Any other dismissal reads "none".
# `RULED` trusts the `JUDGE-RULED:` dismissal message and never detects a
# hand-written one; only dismiss-ruled-review.sh is sanctioned to write it, and
# a hand dismissal carrying the marker violates rules/ci-safety.md
# Judge-Ruled-Review Dismissal Carve-Out.
#
# `requested` reports exactly one fact: a review for that login is still owed
# on a request — pending on the PR, or for Copilot a run in flight (below). It
# separates two states a bare `state: "none"` conflates
# for a REQUEST-TRIGGERED reviewer (Copilot): asked for and not yet answered
# (waiting is meaningful), versus never asked (waiting cannot produce it, and a
# reader without GitHub write scope cannot ask). A developer collecting its own
# current-tip evidence reads that rather than sitting out a budget on a review
# nobody requested (#369).
#
# It is NOT a "has this review been triggered" flag. A delivered review clears
# its own request, so `requested` is false once `state` is set. The policy
# reviewer is PUSH-triggered — review-codex.yml and the fleet App run on the
# push, never on a request — so its `requested` is false on every PR, and an
# in-flight policy review reads `state: "none", requested: false` exactly like
# an unrequested Copilot lane. Resolve a reviewer's arrival by how it is
# triggered (rules/ci-safety.md Always Watch CI), never by this field alone.
#
# Copilot's `requested` is also true while its run is in flight — predicate in
# copilot-run.sh — since a started run consumes the request (#641).
#
# `merge_state.status == "DIRTY"` / `mergeable == "CONFLICTING"` means GitHub
# couldn't create `refs/pull/N/merge` and silently skipped `pull_request:`
# workflows — agent should surface a rebase recommendation rather than keep
# polling `ci.status: none`.

set -euo pipefail

# copilot-run.sh provides copilot_run_in_flight, fetch_requested_logins and
# requested_among, shared with request-copilot-review.sh.
# Sourced, not run, so a caller's in-process `gh` mock reaches it. Command
# substitution strips every trailing newline, so the script directory never
# passes through one bare: parameter expansion derives it (#487), and a
# sentinel carries `pwd` across the strip (#466).
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
# shellcheck source=skills/release/copilot-run.sh
if ! source "${_ppr_dir}/copilot-run.sh"; then
  echo "error: cannot source ${_ppr_dir}/copilot-run.sh — the release skill tree is incomplete; re-clone the repo or re-install the plugin, then re-run" >&2
  exit 2
fi

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
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for review lookup" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" "review" \
    | jq --argjson logins "$logins_json" '
        [.[] | select(.user.login | IN($logins[]))]
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
# skills/release/tests/test_dismiss_ruled_review.sh feeds the message
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

  # `cancel` is NOT failure, and it is NOT a signal at all. Pushing a fix to an
  # open PR auto-cancels the prior SHA's in-flight runs, and a re-dispatched
  # workflow cancels its own earlier run on the same head via concurrency — so
  # a cancelled bucket is a superseded run, and its replacement drives the
  # state. Treating it as failure false-red'd a green PR the instant a reviewer
  # was addressed (#182). So drop cancels, then judge the LIVE buckets: a real
  # `fail` still fails; a `success` alongside a cancelled twin still succeeds
  # (folding cancel into `pending` would have wedged that case). Only when
  # cancels are ALL there is — the replacement not yet registered — fall to
  # pending so the watcher waits for it. `gh pr checks` is head-scoped, so that
  # replacement always concludes and the pending never wedges. `skipping`
  # stays success-equivalent (a skipped required check is a pass), unchanged.
  local ci_status
  ci_status=$(echo "$checks_json" | jq -r '
    (map(select(.bucket != "cancel"))) as $live
    | if (. | length) == 0 then "none"
      elif ($live | any(.bucket == "fail")) then "failure"
      elif ($live | any(.bucket == "pending")) then "pending"
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

  local requested_logins codex_requested copilot_requested
  requested_logins=$(fetch_requested_logins "$owner" "$repo" "$pr_number") \
    || { echo "error: failed to fetch pending review requests for ${owner}/${repo}#${pr_number} — run 'gh auth status' to verify auth, then retry 'gh api repos/${owner}/${repo}/pulls/${pr_number}/requested_reviewers'" >&2; exit 1; }
  codex_requested=$(requested_among "$requested_logins" "${CODEX_REVIEW_LOGINS[@]}") \
    || { echo "error: could not match the policy reviewer against the pending review requests on ${owner}/${repo}#${pr_number} — inspect the list with 'gh api graphql' for that PR's reviewRequests, then re-run this snapshot once it returns an array of reviewer logins" >&2; exit 1; }
  copilot_requested=$(requested_among "$requested_logins" "$COPILOT_REVIEW_LOGIN") \
    || { echo "error: could not match Copilot against the pending review requests on ${owner}/${repo}#${pr_number} — inspect the list with 'gh api graphql' for that PR's reviewRequests, then re-run this snapshot once it returns an array of reviewer logins" >&2; exit 1; }
  # The timeline is read before the Copilot review, so a review posted in
  # between is seen by the review fetch.
  local copilot_started_at=""
  if [[ "$copilot_requested" == false ]]; then
    local timeline flight
    timeline=$(copilot_timeline "$owner" "$repo" "$pr_number") \
      || { echo "error: failed to read whether a Copilot run is in flight on ${owner}/${repo}#${pr_number} — the diagnostic above names the failing read; fix it, then retry" >&2; exit 1; }
    flight=$(copilot_in_flight_state "$timeline")
    copilot_requested=$(printf '%s' "$flight" | jq -r '.in_flight')
    copilot_started_at=$(printf '%s' "$flight" | jq -r '.started_at // empty')
  fi

  local codex_review copilot_review codex_comments copilot_comments
  codex_review=$(latest_review_by   "$owner" "$repo" "$pr_number" "${CODEX_REVIEW_LOGINS[@]}") \
    || { echo "error: failed to fetch Codex review state" >&2; exit 1; }
  copilot_review=$(latest_review_by "$owner" "$repo" "$pr_number" "$COPILOT_REVIEW_LOGIN") \
    || { echo "error: failed to fetch Copilot review state" >&2; exit 1; }
  # Only the policy reviewer's review is ever ruled-dismissed.
  codex_review=$(resolve_ruled_dismissal "$owner" "$repo" "$pr_number" "$codex_review") \
    || { echo "error: failed to read the dismissal of the policy review on ${owner}/${repo}#${pr_number} — inspect 'gh api --paginate repos/${owner}/${repo}/issues/${pr_number}/timeline', then retry" >&2; exit 1; }
  copilot_review=$(printf '%s' "$copilot_review" | jq 'del(._dismissed_review_id)')
  # A Copilot review that posted after the in-flight run started (between
  # the timeline and review reads) is the owed review: nothing is owed now.
  if [[ -n "$copilot_started_at" ]]; then
    copilot_requested=$(printf '%s' "$copilot_review" | jq --arg s "$copilot_started_at" \
      'if (.submitted_at // "") > $s then false else true end')
  fi
  # Resolve each verdict against head — stale reviews collapse to "none".
  codex_review=$(resolve_review_against_head   "$codex_review"   "$head_sha")
  copilot_review=$(resolve_review_against_head "$copilot_review" "$head_sha")
  codex_review=$(printf '%s' "$codex_review"     | jq --argjson r "$codex_requested"   '. + {requested: $r}')
  copilot_review=$(printf '%s' "$copilot_review" | jq --argjson r "$copilot_requested" '. + {requested: $r}')
  codex_comments=$(toplevel_comments_by   "$owner" "$repo" "$pr_number" "${CODEX_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Codex inline comments" >&2; exit 1; }
  copilot_comments=$(toplevel_comments_by "$owner" "$repo" "$pr_number" "${COPILOT_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Copilot inline comments" >&2; exit 1; }

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg head_sha "$head_sha" \
    --arg ci_status "$ci_status" \
    --argjson checks "$checks_json" \
    --argjson codex "$codex_review" \
    --argjson copilot "$copilot_review" \
    --argjson codex_comments "$codex_comments" \
    --argjson copilot_comments "$copilot_comments" \
    --argjson merge_state "$merge_state" \
    '{
      pr_number: $pr_number,
      head_sha: $head_sha,
      ci: {status: $ci_status, checks: $checks},
      reviews: {codex: $codex, copilot: $copilot},
      inline_comments: {codex: $codex_comments, copilot: $copilot_comments},
      merge_state: ($merge_state | {status, mergeable})
    }'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
