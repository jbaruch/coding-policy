#!/usr/bin/env bash
# Shared lane classifier for fleet dedup, release snapshots and dismissals.
# Sourcing defines review_lanes_jq; execution emits the marker as JSON.
set -euo pipefail

CLAUDE_REVIEW_LOGIN="jbaruch-claude-reviewer[bot]"

review_lanes_jq() {
  cat <<'JQ'
# Shared by fleet deduplication, release snapshots and stale-review dismissal.
# Legacy unmarked reviews from the trusted reviewer identities are policy reviews.
def quality_marker: "<!-- coding-policy:claude-quality-review:v1 -->";
def is_quality_review: ((.body // "") | startswith(quality_marker));
def in_review_lane($lane):
  if $lane == "quality" then is_quality_review
  elif $lane == "policy" then (is_quality_review | not)
  else error("unknown review lane: " + $lane) end;
JQ
}

main() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is not installed — install it with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu), then retry" >&2
    return 2
  fi
  jq -n --arg login "$CLAUDE_REVIEW_LOGIN" "$(review_lanes_jq)"' {quality_marker: quality_marker, quality_login: $login}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
