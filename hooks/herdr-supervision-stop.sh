#!/usr/bin/env bash
# Native Claude/Codex Stop gate; only an exact persisted Herdr lead binding
# activates it. stdin/stdout/exit contract: teamlead/supervision_hook.py.
set -euo pipefail

main() {
  local plugin_root
  plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if ! command -v python3 >/dev/null 2>&1; then
    echo 'herdr-supervision-stop: python3 is missing — restore Python to enable the native supervision gate' >&2
    return 0
  fi
  PYTHONPATH="${plugin_root}/skills/herdr-teamlead${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m teamlead.supervision_hook
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
