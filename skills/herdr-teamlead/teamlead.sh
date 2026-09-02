#!/usr/bin/env bash
# Launcher for the team-lead Python utility (`measure`, `plan`, `apply`,
# `state`). It resolves the skill directory, puts it on PYTHONPATH so the
# packaged `teamlead` module imports without an install step, and execs the
# module with every argument forwarded unchanged.
#
# Contract:
#   argv  : forwarded verbatim to `python3 -m teamlead`.
#   stdout: whatever the module emits — one JSON object per subcommand.
#   stderr: diagnostics from this launcher and from the module.
#   exit  : the module's own exit status, or 1 when python3 is absent.
#   env   : PY_BIN overrides the interpreter (default python3); the tests and
#           a venv shim point it elsewhere. PYTHONPATH is prefixed, never
#           replaced. Every other variable the module reads is documented in
#           skills/herdr-teamlead/state-schema.md.
set -euo pipefail

PY_BIN="${PY_BIN:-python3}"

main() {
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    echo "teamlead: '${PY_BIN}' not found on PATH — install Python 3.11+ (\`brew install python@3.11\`) or point PY_BIN at the interpreter" >&2
    return 1
  fi

  if [[ ! -d "${skill_dir}/teamlead" ]]; then
    echo "teamlead: the teamlead package is missing from ${skill_dir} — reinstall the plugin with \`tessl install jbaruch/coding-policy\`" >&2
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  exec "$PY_BIN" -m teamlead "$@"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
