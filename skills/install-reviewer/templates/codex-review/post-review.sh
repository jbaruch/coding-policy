#!/usr/bin/env bash
# Post a Codex policy-review verdict onto a pull request as a GitHub review.
#
# Reads the structured result Codex produced (the --output-last-message file,
# shaped by .github/codex-review/schema.json) and submits ONE pull-request
# review whose body carries the summary and every finding. Findings ride in
# the review body rather than as inline comments so an off-diff line can never
# trigger the HTTP 422 that would cascade-fail the whole review.
#
# The review is authored by whoever GH_TOKEN belongs to. A clean pass posts
# APPROVE and a violation posts REQUEST_CHANGES. A token GitHub forbids from
# approving (`github-actions[bot]` — HTTP 422) falls back to COMMENT on a pass,
# so the verdict is never lost.
#
# Usage: post-review.sh <owner> <repo> <pr-number> <result-json-file>
# Out:   one JSON object on stdout: {"state":"posted","event":"...","findings":N}
# Exit:  0 on success; non-zero with a stderr diagnostic on failure.

set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# Submit ONE review event to the PR, retrying transient failures with backoff.
# Args: <owner> <repo> <pr> <event> <body>
# Return: 0 posted; 22 the event was rejected as unprocessable (HTTP 422 — e.g.
#   an APPROVE from a token that cannot approve); 1 other failure after retries.
submit_review() {
  local owner="$1" repo="$2" pr="$3" event="$4" body="$5"
  local payload attempt=0 max=3 err
  payload=$(jq -n --arg event "$event" --arg body "$body" '{event: $event, body: $body}')
  while :; do
    attempt=$((attempt + 1))
    # Capture stderr (stdout discarded) so a 422 can be told from a transient 5xx.
    if err=$(printf '%s' "$payload" | gh api "repos/${owner}/${repo}/pulls/${pr}/reviews" --method POST --input - 2>&1 1>/dev/null); then
      return 0
    fi
    # HTTP 422 (unprocessable) is not transient — do not retry; let the caller fall back.
    if grep -q "HTTP 422" <<<"$err"; then
      printf '%s\n' "$err" >&2
      return 22
    fi
    if (( attempt >= max )); then
      printf '%s\n' "$err" >&2
      echo "error: failed to submit the review on ${owner}/${repo}#${pr} after ${max} attempts — see the gh error above (token scope, PR state, or a GitHub API outage)" >&2
      return 1
    fi
    echo "post-review: submit attempt ${attempt} failed — retrying in $(( attempt * 5 ))s" >&2
    sleep $(( attempt * 5 ))
  done
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number> <result-json-file>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr="$3" result="$4"

  if [[ ! -f "$result" ]]; then
    echo "error: result file not found: ${result} — codex exec did not write its --output-last-message file; check the review step logs" >&2
    exit 1
  fi
  if ! jq -e . "$result" >/dev/null 2>&1; then
    echo "error: ${result} is not valid JSON — codex exec did not honor --output-schema; inspect the file and the review step logs" >&2
    exit 1
  fi

  local verdict summary findings_count
  verdict=$(jq -r '.verdict // "pass"' "$result")
  summary=$(jq -r '.summary // ""' "$result")
  findings_count=$(jq '(.findings // []) | length' "$result")

  local event
  case "$verdict" in
    changes_requested) event="REQUEST_CHANGES" ;;
    pass)              event="APPROVE" ;;
    *) echo "error: unexpected verdict '${verdict}' in ${result} (want pass|changes_requested)" >&2; exit 1 ;;
  esac

  # Build the review body: summary, then a findings list (omitted when clean).
  local body findings_md
  body="$summary"
  if (( findings_count > 0 )); then
    findings_md=$(jq -r '.findings[] | "- `\(.path):\(.line)` — **\(.rule)** — \(.message)"' "$result")
    body="${body}"$'\n\n'"## Findings"$'\n'"${findings_md}"
  fi

  # A pass is APPROVE when the token can approve (a GitHub App); a token that
  # cannot approve (github-actions[bot] — HTTP 422) falls back to COMMENT so the
  # verdict is never lost. The output reports the event actually posted.
  local posted="$event" rc=0
  submit_review "$owner" "$repo" "$pr" "$event" "$body" || rc=$?
  if (( rc == 22 )) && [[ "$event" == "APPROVE" ]]; then
    echo "post-review: APPROVE rejected as unprocessable (HTTP 422) — this token cannot approve; posting COMMENT instead" >&2
    posted="COMMENT"
    submit_review "$owner" "$repo" "$pr" "COMMENT" "$body" || exit 1
  elif (( rc != 0 )); then
    exit 1
  fi

  jq -n --arg event "$posted" --argjson findings "$findings_count" \
    '{state: "posted", event: $event, findings: $findings}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
