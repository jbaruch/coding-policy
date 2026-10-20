#!/usr/bin/env bash
# Watch CI, mergeability and both current-head review lanes until terminal.
# Either lane's CHANGES_REQUESTED blocks. Advisory findings never block; both
# posted review bodies must still arrive and be read. Both lanes are
# PR-event triggered, so no manual reviewer request is required.
#
# Usage: watch-pr-reviews.sh OWNER REPO PR
# stdout: snapshot plus watch {result, attempts, elapsed_seconds}.
# rc 0: ready, changes_requested, ci_failure or dirty (act on result).
# rc 1: pending_at_budget (inspect missing signal and retry after recovery).
# rc 2: setup/poll failure, stderr diagnostic. Script-owned interval and budget
# are overridable for tests; never wrap a watch in an invented timeout.

set -euo pipefail

INTERVAL_SEC="${WATCH_PR_REVIEWS_INTERVAL_SEC:-15}"
BUDGET_SEC="${WATCH_PR_REVIEWS_BUDGET_SEC:-900}"

# Command substitution strips every trailing newline, so the script directory
# never passes through one bare: parameter expansion derives it (#487), and a
# sentinel carries `pwd` across the strip (#466).
case "${BASH_SOURCE[0]}" in
  */*) _script_src="${BASH_SOURCE[0]%/*}" ;;
  *) _script_src=. ;;
esac
if ! SCRIPT_DIR="$(CDPATH='' cd -- "${_script_src:-/}" && pwd && printf x)"; then
  echo "error: cannot enter the script directory ${_script_src:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
  exit 2
fi
SCRIPT_DIR="${SCRIPT_DIR%x}"
SCRIPT_DIR="${SCRIPT_DIR%$'\n'}"
POLL_CMD="${WATCH_PR_REVIEWS_POLL_CMD:-${SCRIPT_DIR}/poll-pr-reviews.sh}"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

validate_positive_int() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: ${name} must be a positive integer, got: '${value}'" >&2
    exit 2
  fi
}

# Overridable in tests so the loop can be driven off queued snapshots
# without a live gh. In production this shells out to poll-pr-reviews.sh
# (invoked via `bash <path>` — tessl packaging strips the exec bit).
fetch_snapshot() {
  local owner="$1" repo="$2" pr="$3"
  bash "$POLL_CMD" "$owner" "$repo" "$pr"
}

emit_and_exit() {
  local result="$1" attempts="$2" elapsed="$3" snapshot="$4" rc="$5"
  printf '%s' "$snapshot" | jq \
    --arg result "$result" \
    --argjson attempts "$attempts" \
    --argjson elapsed "$elapsed" \
    '. + {watch: {result: $result, attempts: $attempts, elapsed_seconds: $elapsed}}'
  exit "$rc"
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  validate_positive_int "WATCH_PR_REVIEWS_INTERVAL_SEC" "$INTERVAL_SEC"
  validate_positive_int "WATCH_PR_REVIEWS_BUDGET_SEC" "$BUDGET_SEC"
  if (( INTERVAL_SEC > BUDGET_SEC )); then
    echo "error: WATCH_PR_REVIEWS_INTERVAL_SEC (${INTERVAL_SEC}) cannot exceed WATCH_PR_REVIEWS_BUDGET_SEC (${BUDGET_SEC})" >&2
    exit 2
  fi

  local owner="$1" repo="$2" pr="$3"
  local elapsed=0 attempts=0 snapshot=""

  # Loop bound is `<= BUDGET_SEC` (not strictly less) so a signal that lands
  # exactly at the budget boundary is still caught by the final poll.
  while (( elapsed <= BUDGET_SEC )); do
    attempts=$(( attempts + 1 ))

    snapshot=$(fetch_snapshot "$owner" "$repo" "$pr") \
      || { echo "error: poll-pr-reviews.sh failed for ${owner}/${repo}#${pr} (attempt ${attempts}) — run 'bash ${POLL_CMD} ${owner} ${repo} ${pr}' directly to see the underlying gh/jq error; the watch is idempotent, re-run once resolved" >&2; exit 2; }

    if ! printf '%s' "$snapshot" | jq -e . >/dev/null 2>&1; then
      echo "error: poll-pr-reviews.sh returned non-JSON for ${owner}/${repo}#${pr} (attempt ${attempts}): ${snapshot}" >&2
      exit 2
    fi

    local mergeable mstatus ci codex claude
    mergeable=$(printf '%s' "$snapshot" | jq -r '.merge_state.mergeable')
    mstatus=$(printf '%s'   "$snapshot" | jq -r '.merge_state.status')
    ci=$(printf '%s'        "$snapshot" | jq -r '.ci.status')
    codex=$(printf '%s'     "$snapshot" | jq -r '.reviews.codex.state')
    claude=$(printf '%s' "$snapshot" | jq -r '.reviews.claude.state // "none"')

    # Order matters: a conflicting branch or a failed check or a blocking
    # CHANGES_REQUESTED verdict is terminal-for-this-round — the agent must
    # go act (rebase / fix / address) before any further waiting helps.
    if [[ "$mergeable" == "CONFLICTING" || "$mstatus" == "DIRTY" ]]; then
      emit_and_exit "dirty" "$attempts" "$elapsed" "$snapshot" 0
    fi
    if [[ "$ci" == "failure" ]]; then
      emit_and_exit "ci_failure" "$attempts" "$elapsed" "$snapshot" 0
    fi
    # Both lanes derive CHANGES_REQUESTED from blocking findings. Advisory
    # findings approve or comment and must be read, but do not block the merge.
    if [[ "$codex" == "CHANGES_REQUESTED" || "$claude" == "CHANGES_REQUESTED" ]]; then
      emit_and_exit "changes_requested" "$attempts" "$elapsed" "$snapshot" 0
    fi

    # Both current-head verdicts must have arrived; findings remain readable
    # in the snapshot. Blocking findings returned above.
    if [[ "$mergeable" == "MERGEABLE" \
       && ( "$ci" == "success" || "$ci" == "none" ) \
       && "$codex" != "none" \
       && "$claude" != "none" ]]; then
      emit_and_exit "ready" "$attempts" "$elapsed" "$snapshot" 0
    fi

    # Still pending (UNKNOWN mergeability, CI pending, or a bot yet to post).
    # Skip the trailing sleep on the boundary iteration; otherwise cap the
    # sleep at remaining budget so total sleep tracks BUDGET_SEC.
    (( elapsed == BUDGET_SEC )) && break
    local remaining=$(( BUDGET_SEC - elapsed ))
    local sleep_for=$INTERVAL_SEC
    (( sleep_for > remaining )) && sleep_for=$remaining
    sleep "$sleep_for"
    elapsed=$(( elapsed + sleep_for ))
  done

  emit_and_exit "pending_at_budget" "$attempts" "$elapsed" "$snapshot" 1
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
