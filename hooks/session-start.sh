#!/usr/bin/env bash
# Run every SessionStart hook and deliver all of their statuses at once.
#
# The plugin declares this script as its only SessionStart hook. `tessl hook run`
# keeps only the LAST hook's output in a group, so with several hooks each one
# overwrote the one before it, and a silent last hook (the usual case) erased
# every status: nothing any SessionStart hook reported reached a session. One
# entry point that merges the statuses itself is the only shape that delivers
# them all.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read; each hook gets /dev/null.
#   stdout: one JSON object {"additionalContext": "<statuses>"} joining every
#           hook's additionalContext with a blank line, in HOOKS order. Nothing
#           when no hook reported. A hook that exits non-zero or prints
#           something other than one JSON object is reported as its own
#           "Session-start status — hook <name> ..." line, never dropped.
#   stderr: each hook's stderr, passed through.
#   exit  : always 0 — a failed hook is reported, never a failed session start.
#   env   : SESSION_START_HOOKS overrides the hook list (tests only).
set -euo pipefail

#: Run in this order. Each is a script in this directory emitting at most one
#: {"additionalContext": ...} object and exiting 0.
HOOKS=(check-git-sync check-tessl-latest herdr-team-status check-leftover-worktrees)

warn() { printf 'session-start: %s\n' "$1" >&2; }

main() {
  local here name out rc
  local -a hooks outputs=()
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { warn "cannot resolve the hooks directory"; return 0; }
  command -v python3 >/dev/null || { warn "python3 not found on PATH — install it so session start can report status"; return 0; }
  read -r -a hooks <<<"${SESSION_START_HOOKS:-${HOOKS[*]}}"

  for name in "${hooks[@]}"; do
    rc=0
    out="$(bash "${here}/${name}.sh" </dev/null)" || rc=$?
    outputs+=("$name" "$rc" "$out")
  done

  python3 - "${outputs[@]}" <<'PY'
import json
import sys

args = sys.argv[1:]
statuses = []
for i in range(0, len(args), 3):
    name, rc, out = args[i], args[i + 1], args[i + 2].strip()
    if rc != "0":
        statuses.append("Session-start status — hook {} exited {}; run `bash hooks/{}.sh` "
                        "in this repo to see why.".format(name, rc, name))
        continue
    if not out:
        continue
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
    context = doc.get("additionalContext") if isinstance(doc, dict) else None
    if not isinstance(context, str):
        statuses.append("Session-start status — hook {} printed something other than one "
                        "additionalContext object; run `bash hooks/{}.sh` in this repo to see it.".format(name, name))
        continue
    statuses.append(context)
if statuses:
    print(json.dumps({"additionalContext": "\n\n".join(statuses)}))
PY
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no session-start status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
  fi
  exit 0
fi
