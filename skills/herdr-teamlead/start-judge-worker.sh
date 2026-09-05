#!/usr/bin/env bash
# Start and verify the judge tier recorded by teamlead plan.
# Contract: <plan-file> <pane> [claude|codex|grok].
# stdout: JSON {agent, model, effort, pane, argv_verified, verified} on success.
# Exit 0: launch arguments proved the tier; 1: input/transport/proof failure;
# 2: command-line usage error. No banner or transcript text establishes proof.
# HERDR_BIN overrides the transport; Python selection is owned by teamlead.sh.
set -euo pipefail

main() {
  if (( $# < 2 || $# > 3 )); then
    echo "start-judge-worker: usage: start-judge-worker.sh <plan-file> <pane> [kind]" >&2
    return 2
  fi
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bash "${skill_dir}/teamlead.sh" start-judge --assignments "$1" --pane "$2" \
    --kind "${3:-claude}" --herdr-bin "${HERDR_BIN:-herdr}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
