#!/usr/bin/env bash
# Print the version a Tessl publish landed, keeping "did not land" apart from
# "cannot tell yet".
#
# Wraps verify-publish-landed.sh so the release flow has one command rather
# than an rc dispatch for a caller to retype (rules/script-delegation.md
# "Scripts Are Real Files"). The dispatch is the point: rc 1 is an answer about
# the publish and rc 2 is the absence of one, their recoveries differ, and a
# caller that collapses them reports an unreachable `gh` as a failed release.
#
# Usage: confirm-tessl-landed.sh <workspace> <plugin> <pre-baseline> <run-id>
# Exit 0: the landed version on stdout.
# Exit 1: the publish did not land; the helper's reason on stderr.
# Exit 2: indeterminate — the run is not terminal, or a tool is unreachable.
#         Never a landing: a caller that cannot tell must not claim one.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

main() {
  if [ "$#" -ne 4 ]; then
    echo "usage: confirm-tessl-landed.sh <workspace> <plugin> <pre-baseline> <run-id>" >&2
    return 2
  fi
  if ! command -v jq >/dev/null 2>&1; then
    echo "confirm-tessl-landed: jq is not installed; install it before confirming a publish" >&2
    return 2
  fi
  local landed status reason current
  status=0
  landed="$(bash "${here}/verify-publish-landed.sh" "$1" "$2" "$3" "$4")" || status=$?
  case "$status" in
    0) ;;
    1)
      reason="$(printf '%s' "$landed" | jq -r '.reason // "see the diagnostic above"' 2>/dev/null || printf 'see the diagnostic above')"
      echo "confirm-tessl-landed: the publish did not land — ${reason}" >&2
      return 1
      ;;
    *)
      echo "confirm-tessl-landed: cannot tell whether the publish landed (exit ${status}) — re-run once the publish run is terminal and gh/tessl are reachable. Do not report a release on this" >&2
      return 2
      ;;
  esac
  # Parse failure and a well-formed envelope missing `.current` are different
  # repairs, so they get different diagnostics rather than one that guesses.
  if ! current="$(printf '%s' "$landed" | jq -r '.current // empty')"; then
    echo "confirm-tessl-landed: landed payload is not JSON: ${landed}. Run 'bash ${here}/verify-publish-landed.sh $1 $2 $3 $4' directly and repair its output contract — an exit-0 envelope is one JSON object — before retrying the confirmation" >&2
    return 2
  fi
  if [ -z "$current" ]; then
    echo "confirm-tessl-landed: landed payload carries no .current: ${landed}. Run 'bash ${here}/verify-publish-landed.sh $1 $2 $3 $4' directly and repair its envelope — an exit-0 envelope carries .current — before retrying the confirmation" >&2
    return 2
  fi
  printf '%s\n' "$current"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
