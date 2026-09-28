#!/usr/bin/env bash
# Native Claude/Codex Stop gate; only an exact persisted Herdr foreman binding
# activates it. stdin/stdout/exit contract: foreman/supervision_hook.py.
set -euo pipefail

main() {
  local src dir plugin_root
  # Command substitution strips every trailing newline, so neither the hooks
  # directory nor the plugin root passes through one bare: parameter expansion
  # derives the first, and a sentinel carries `pwd` across the strip (#487).
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  if ! plugin_root="$(cd -- "${dir:-/}/.." && pwd && printf x)"; then
    echo "herdr-supervision-stop: cannot enter the plugin root above ${dir:-/} — reinstall the plugin to enable the native supervision gate" >&2
    return 0
  fi
  plugin_root="${plugin_root%x}"
  plugin_root="${plugin_root%$'\n'}"
  if ! command -v python3 >/dev/null 2>&1; then
    echo 'herdr-supervision-stop: python3 is missing — restore Python to enable the native supervision gate' >&2
    return 0
  fi
  local rc=0
  # PYTHONDONTWRITEBYTECODE: the evaluator only reads, but importing it would
  # drop __pycache__ into the installed plugin tree, which the read-only
  # contract does not allow.
  PYTHONPATH="${plugin_root}/skills/herdr-foreman${PYTHONPATH:+:${PYTHONPATH}}" \
  PYTHONDONTWRITEBYTECODE=1 \
    python3 -m foreman.supervision_hook || rc=$?
  if (( rc != 0 )); then
    echo "herdr-supervision-stop: Python hook failed (exit ${rc}) before completing its contract — restore the hook installation; native gating requires its structured JSON result" >&2
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
