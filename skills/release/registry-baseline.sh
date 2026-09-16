#!/usr/bin/env bash
# Print the registry baseline a release is measured against, or fail loudly.
#
# Wraps capture-registry-baseline.sh so the release flow has one command rather
# than a capture, an extraction and an emptiness check for a caller to retype
# (rules/script-delegation.md "Scripts Are Real Files"). Each of those steps
# fails in its own way and all three must stop the release: an unparseable
# baseline, an absent `jq`, or a payload without `.version` each leave the
# caller with an empty PRE, and an empty baseline passes the publish contract's
# second conjunct VACUOUSLY — reporting a publish that never happened
# (rules/ci-safety.md "Always Watch CI").
#
# Usage: registry-baseline.sh <workspace> <plugin>
# Exit 0: the version on stdout, nothing else.
# Exit 2: a usage error, or a baseline this cannot vouch for; diagnostic on
#         stderr. There is no exit 1: the script has no verdict to return, only
#         a value or the inability to produce one.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

main() {
  if [ "$#" -ne 2 ]; then
    echo "usage: registry-baseline.sh <workspace> <plugin>" >&2
    return 2
  fi
  local workspace="$1" plugin="$2" payload version
  if ! command -v jq >/dev/null 2>&1; then
    echo "registry-baseline: jq is not installed; install it before capturing a baseline" >&2
    return 2
  fi
  if ! payload="$(bash "${here}/capture-registry-baseline.sh" "$workspace" "$plugin")"; then
    echo "registry-baseline: could not read the registry baseline for ${workspace}/${plugin} — see the diagnostic above" >&2
    return 2
  fi
  if ! version="$(printf '%s' "$payload" | jq -r '.version // empty')"; then
    echo "registry-baseline: baseline payload is not JSON: ${payload}" >&2
    return 2
  fi
  if [ -z "$version" ]; then
    echo "registry-baseline: baseline payload carries no .version: ${payload}. An empty baseline passes the registry-advance conjunct vacuously, so the release stops here" >&2
    return 2
  fi
  printf '%s\n' "$version"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
