#!/usr/bin/env bash
# Run every SessionStart hook and deliver all of their statuses at once.
#
# The plugin declares this script as its only SessionStart hook, as a native
# hook for Claude Code and Codex. Two `tessl hook run` behaviours rule out the
# portable form: it keeps only the LAST hook's output in a group, and it hands a
# hook only HOME, PATH, TMPDIR and TESSL_* from the environment, so HERDR_ENV
# never reached the hooks that decide on it. One native entry that merges the
# statuses itself delivers them all, with the session's environment intact.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read; each hook gets /dev/null.
#   stdout: one native payload {"hookSpecificOutput": {"hookEventName":
#           "SessionStart", "additionalContext": "<statuses>"}} joining every
#           hook's additionalContext with a blank line, in HOOKS order. Nothing
#           when no hook reported. A hook that exits non-zero or prints
#           something other than one additionalContext object is reported as
#           its own "Session-start status — hook <name> ..." line naming the
#           absolute path to run, never dropped.
#   stderr: each hook's stderr, passed through, plus this script's warnings.
#   exit  : always 0 — a failed hook is reported, never a failed session start.
#   needs : python3 or jq to read and encode JSON (python3 first). The hooks run
#           either way; with neither, the statuses are lost and a warning says so.
#   env   : SESSION_START_HOOKS overrides the hook list (tests only).
set -euo pipefail

#: Run in this order. Each is a script in this directory emitting at most one
#: {"additionalContext": ...} object and exiting 0.
HOOKS=(check-git-sync check-tessl-latest herdr-team-status check-leftover-worktrees)

warn() { printf 'session-start: %s\n' "$1" >&2; }

#: The JSON tool in use: python3, jq, or empty when neither is on PATH.
JSON_TOOL=""

# Print the additionalContext string of one hook output; exit 1 when the output
# is not one object carrying a string additionalContext.
context_of() { # <output>
  if [[ "$JSON_TOOL" == python3 ]]; then
    python3 -c '
import json, sys
try:
    doc = json.loads(sys.argv[1])
except ValueError:
    sys.exit(1)
ctx = doc.get("additionalContext") if isinstance(doc, dict) else None
if not isinstance(ctx, str):
    sys.exit(1)
sys.stdout.write(ctx)
' "$1"
  else
    # Raw slurp plus `fromjson?`: an output that is not JSON is the expected
    # non-result and yields no value (exit 4 under -e); a real jq error still
    # prints its own diagnostic.
    jq -e -R -s -j 'fromjson? | select(type == "object" and (.additionalContext | type) == "string") | .additionalContext' <<<"$1"
  fi
}

# Print the native SessionStart payload Claude Code and Codex both read.
encode() { # <text>
  if [[ "$JSON_TOOL" == python3 ]]; then
    python3 -c 'import json, sys; print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": sys.argv[1]}}))' "$1"
  else
    jq -n --arg c "$1" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}'
  fi
}

main() {
  local here name out rc ctx joined=""
  local -a hooks statuses=() names=() codes=() outputs=()
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { warn "cannot resolve the hooks directory"; return 0; }
  read -r -a hooks <<<"${SESSION_START_HOOKS:-${HOOKS[*]}}"

  # Run every hook first, so a missing JSON tool never stops one from acting.
  for name in "${hooks[@]}"; do
    rc=0
    out="$(bash "${here}/${name}.sh" </dev/null)" || rc=$?
    names+=("$name"); codes+=("$rc"); outputs+=("$out")
  done

  if command -v python3 >/dev/null; then
    JSON_TOOL=python3
  elif command -v jq >/dev/null; then
    JSON_TOOL=jq
  else
    warn "neither python3 nor jq is on PATH — the hooks ran, but their statuses cannot be delivered; install one of them"
    return 0
  fi

  local i
  for i in "${!names[@]}"; do
    name="${names[$i]}"
    if [[ "${codes[$i]}" != 0 ]]; then
      statuses+=("Session-start status — hook ${name} exited ${codes[$i]}; run \`bash ${here}/${name}.sh\` from this repo to see why.")
      continue
    fi
    [[ -n "${outputs[$i]//[[:space:]]/}" ]] || continue
    if ! ctx="$(context_of "${outputs[$i]}")"; then
      statuses+=("Session-start status — hook ${name} printed something other than one additionalContext object; run \`bash ${here}/${name}.sh\` from this repo to see it.")
      continue
    fi
    statuses+=("$ctx")
  done

  (( ${#statuses[@]} > 0 )) || return 0
  for i in "${!statuses[@]}"; do
    (( i > 0 )) && joined+=$'\n\n'
    joined+="${statuses[$i]}"
  done
  if ! encode "$joined"; then
    warn "${JSON_TOOL} failed to encode the merged status — this session gets none; run 'bash ${here}/session-start.sh' to see why"
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no session-start status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
  fi
  exit 0
fi
