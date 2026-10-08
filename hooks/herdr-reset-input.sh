#!/usr/bin/env bash
# Native Claude/Codex and Grok's Claude-compatible UserPromptSubmit gate.
# Contract: foreman/reset_input_hook.py.
set -euo pipefail

main() {
  local src dir root rc=0
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  if ! root="$(CDPATH='' cd -- "${dir:-/}/.." && pwd && printf x)"; then
    printf '%s\n' '{"decision":"block","reason":"Restore the Herdr reset hook installation before submitting this prompt."}'
    return 0
  fi
  root="${root%x}"
  root="${root%$'\n'}"
  if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '{"decision":"block","reason":"Restore Python to verify Herdr reset input before submitting this prompt."}'
    return 0
  fi
  if ! cd -- "${root}/skills/herdr-foreman"; then
    printf '%s\n' '{"decision":"block","reason":"Restore the installed Herdr skill directory before submitting this prompt."}'
    return 0
  fi
  # Neither checkout modules nor an inherited PYTHONPATH may replace this
  # installed verifier. Native cwd authority comes from the hook payload.
  PYTHONPATH="${root}/skills/herdr-foreman" PYTHONDONTWRITEBYTECODE=1 \
    python3 -s -m foreman.reset_input_hook || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' '{"decision":"block","reason":"The Herdr reset hook failed; restore its installation and inspect the reset record before submitting this prompt."}'
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
