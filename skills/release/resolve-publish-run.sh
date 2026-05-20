#!/usr/bin/env bash
# Resolve the publish-workflow run that corresponds to a given merge
# commit, polling until the run is enqueued and listed.
#
# After `gh pr merge`, the publish workflow may take several seconds
# to be enqueued and visible to `gh run list`. A single immediate
# lookup can return empty, which would then error `gh run watch <id>`
# downstream. This script polls the listing on a 2s interval up to a
# 30s budget and prints the matching run's database ID once it
# appears. Filters on `headSha == <merge-sha>` AND `event == push` so
# manual `workflow_dispatch` runs sharing the SHA are excluded — same
# filter the interactive Step 7 lookup uses.
#
# Usage: resolve-publish-run.sh <owner> <repo> <merge-sha> <workflow-name>
# Out:   one line on stdout: the publish run's database ID (integer)
# Exit:  0 on success; non-zero with stderr diagnostic if the run never
#        appears within the budget or any `gh` call fails

set -euo pipefail

INTERVAL_SEC="${RESOLVE_PUBLISH_RUN_INTERVAL_SEC:-2}"
BUDGET_SEC="${RESOLVE_PUBLISH_RUN_BUDGET_SEC:-30}"

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <owner> <repo> <merge-sha> <workflow-name>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" merge_sha="$3" workflow="$4"
  local elapsed=0 run_id

  while (( elapsed < BUDGET_SEC )); do
    run_id=$(gh run list \
      --repo "${owner}/${repo}" \
      --branch main \
      --workflow "$workflow" \
      --json databaseId,headSha,event \
      --jq '.[] | select(.headSha == "'"$merge_sha"'") | select(.event == "push") | .databaseId' \
      | head -n 1)
    if [[ -n "$run_id" ]]; then
      echo "$run_id"
      return 0
    fi
    sleep "$INTERVAL_SEC"
    elapsed=$(( elapsed + INTERVAL_SEC ))
  done

  echo "error: no '${workflow}' push-event run found for merge SHA ${merge_sha} on ${owner}/${repo} after ${BUDGET_SEC}s — inspect 'gh run list --repo ${owner}/${repo} --branch main --workflow \"${workflow}\"' to diagnose (workflow may have failed to enqueue, or the SHA may not have triggered it)" >&2
  exit 1
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
