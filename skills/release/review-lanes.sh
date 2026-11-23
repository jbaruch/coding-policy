#!/usr/bin/env bash
# Shared lane classifier for fleet dedup and release snapshots.
# With no arguments emit lane metadata. With state HEAD, read native review
# pages on stdin and emit the bound quality state (or "none") as JSON.
set -euo pipefail

CLAUDE_REVIEW_LOGIN="jbaruch-claude-reviewer[bot]"
case "${BASH_SOURCE[0]}" in
  */*) _lane_dir="${BASH_SOURCE[0]%/*}" ;;
  *) _lane_dir=. ;;
esac
QUALITY_SELECTOR="${_lane_dir:-/}/latest_quality_review.py"

review_lanes_jq() {
  cat <<'JQ'
# Legacy unmarked reviews from the trusted reviewer identities are policy reviews.
def quality_marker: "<!-- coding-policy:claude-quality-review:v1 -->";
def is_quality_review: ((.body // "") | startswith(quality_marker));
def readable_quality_body:
  if .body == null or (.body | type == "string") then .
  else error("Invalid quality review body; inspect the native review text and retry") end;
def quality_review_at($head):
  .commit_id == $head and ($head | type == "string" and test("\\A[0-9a-f]{40}\\z"))
  and (.body | type == "string") and is_quality_review
  and ([.body | splits("[\r\n]") | select(test("^\\s*Reviewed commit:"; "i"))]
       == ["Reviewed commit: \($head). Code quality and bugs only."]);
def live_quality_review($head):
  (.state | IN("APPROVED", "COMMENTED", "CHANGES_REQUESTED"))
  and quality_review_at($head);
def in_review_lane($lane):
  if $lane == "quality" then is_quality_review
  elif $lane == "policy" then (is_quality_review | not)
  else error("unknown review lane: " + $lane) end;
JQ
}

require_quality_selector() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: Python 3 is required for quality review lookup — install python3 and make it available on PATH, then retry" >&2
    return 2
  fi
  if [[ ! -f "$QUALITY_SELECTOR" || ! -r "$QUALITY_SELECTOR" ]]; then
    echo "error: cannot read $QUALITY_SELECTOR — restore the complete coding-policy checkout or reinstall the plugin, then retry" >&2
    return 2
  fi
}

quality_review_for_head() {
  local reviews
  reviews=$(python3 "$QUALITY_SELECTOR" "$CLAUDE_REVIEW_LOGIN") || return
  jq -s --arg head "$1" "$(review_lanes_jq)"'
    if length == 1 and (.[0] | type == "array" and all(.[]; type == "object")) then .[0]
    else error("Expected one quality review array; reinstall the matching coding-policy release helpers") end
    | last as $latest
    | if $latest.state == "COMMENTED" and ($latest | quality_review_at($head)) then
        # A comment leaves an earlier same-head change request active. An
        # approval supersedes it, and a dismissed request has no live state.
        (map(select((.state == "CHANGES_REQUESTED" and .commit_id == $head)
                    or (.state == "APPROVED" and quality_review_at($head)))) | last) as $decisive
        | if $decisive.state == "CHANGES_REQUESTED" then $decisive else $latest end
      else $latest end' <<< "$reviews"
}

latest_live_quality_state() {
  quality_review_for_head "$1" | jq -r --arg head "$1" "$(review_lanes_jq)"'
    if live_quality_review($head) then .state else "none" end'
}

main() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is not installed — install it with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu), then retry" >&2
    return 2
  fi
  if [[ $# == 2 && "$1" == state ]]; then
    require_quality_selector || return
    latest_live_quality_state "$2" | jq -R .
    return
  fi
  case $# in
    0) jq -n --arg login "$CLAUDE_REVIEW_LOGIN" "$(review_lanes_jq)"' {quality_marker: quality_marker, quality_login: $login}' ;;
    *) echo "usage: review-lanes.sh [state HEAD]" >&2; return 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
