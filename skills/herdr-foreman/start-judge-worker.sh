#!/usr/bin/env bash
# Start and verify the judge tier recorded by foreman plan.
# Contract: <plan-file> <pane> [claude|codex|grok] [--state FILE] [--task TASK] [--now ISO].
# Requires an empty shell pane; use apply for an existing worker's relaunch.
# stdout: JSON {agent, model, effort, pane, argv_verified, verified} on success.
# Exit 0: launch arguments proved the tier; 1: input/transport/proof failure;
# 2: command-line usage error. No banner or transcript text establishes proof.
# HERDR_BIN overrides the transport; Python selection is owned by foreman.sh.
set -euo pipefail

main() {
  if (( $# < 2 )); then
    echo "start-judge-worker: usage: start-judge-worker.sh <plan-file> <pane> [kind] [--state FILE] [--task TASK] [--now ISO]" >&2
    return 2
  fi
  local skill_dir skill_src
  # Command substitution strips every trailing newline, so the script directory
  # never passes through one bare: parameter expansion derives it (#487), and a
  # sentinel carries `pwd` across the strip (#466).
  case "${BASH_SOURCE[0]}" in
    */*) skill_src="${BASH_SOURCE[0]%/*}" ;;
    *) skill_src=. ;;
  esac
  if ! skill_dir="$(cd -- "${skill_src:-/}" && pwd && printf x)"; then
    echo "start-judge-worker: cannot enter the script directory ${skill_src:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
    return 1
  fi
  skill_dir="${skill_dir%x}"
  skill_dir="${skill_dir%$'\n'}"
  local plan="$1" pane="$2" kind="claude"
  shift 2
  if (( $# > 0 )) && [[ "$1" != --* ]]; then
    kind="$1"
    shift
  fi
  bash "${skill_dir}/foreman.sh" start-judge --assignments "$plan" --pane "$pane" \
    --kind "$kind" --herdr-bin "${HERDR_BIN:-herdr}" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
